"""Web 会话管理器：包装 agent 循环，桥接 confirm 回调到 WebSocket。

架构（事件流）：
  chat_turn_stream (后台线程)
    → EventBus.emit()
    → _on_event (总线订阅者，call_soon_threadsafe)
    → asyncio.Queue (跨线程安全)
    → sender 协程 (app.py 中)
    → WebSocket → 浏览器

  浏览器 → WebSocket → receiver 协程 (app.py 中)
    → respond_confirm() (线程安全)
    → threading.Event.set()
    → agent 线程解除阻塞 → 继续产生事件

线程安全要点：
- threading.Event + dict 用于 confirm 桥接（同步线程 ↔ 异步协程）
- asyncio.Queue 用于事件跨线程传递（通过 call_soon_threadsafe 入队）
- 每个 WebSession 实例绑定一个 WebSocket 连接（一个浏览器标签页 = 一个会话）
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import asdict
from typing import Any

from ..agent import AlfredDeps, build_agent, chat_turn_stream
from ..config import Config
from ..events import (
    AssistantChunk,
    ContextCompacted,
    EventBus,
    ToolCallEnd,
    ToolCallStart,
    ToolDenied,
    TurnEnd,
    TurnError,
    TurnRetrying,
    TurnStart,
)
from ..history import Session
from ..memory.blocks import MemoryBlocks
from ..rules.loader import render_rules, scan_rules
from ..skills.loader import render_skills_index, scan_skills

logger = logging.getLogger(__name__)

# confirm 超时：5 分钟
_CONFIRM_TIMEOUT_S = 300


class WebSession:
    """一个 Web 会话 = 一个浏览器标签页 + 一个 JSONL 会话文件。

    生命周期：
    1. 创建实例（绑定 session_id 和 config）
    2. start_turn(user_input) — 启动后台线程运行对话轮次
    3. event_queue — 异步事件队列，供 sender 协程读取
    4. respond_confirm(id, approved) — 响应确认请求
    5. get_messages() — 获取会话历史（REST API 用）
    """

    def __init__(self, config: Config, session_id: str | None = None):
        self.config = config
        self.session = Session(config, session_id=session_id)
        self.agent = build_agent(config)
        self.blocks = MemoryBlocks(config)

        # 异步事件队列（sender 协程读取）
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        # confirm 桥接（线程安全）
        self._pending_confirm: dict[str, Any] | None = None
        self._confirm_lock = threading.Lock()

        # 当前事件循环引用
        self._loop: asyncio.AbstractEventLoop | None = None

        # 预加载静态数据（与 CLI chat 命令一致）
        self._load_static_data()

    def _load_static_data(self) -> None:
        """一次性读好技能索引、教训、规则、笔记目录、代码库索引。"""
        self._skill_index = render_skills_index(scan_skills(self.config))
        self._lessons_text = self._load_lessons_text()
        _always_rules, _recall_rules = render_rules(scan_rules(self.config))
        self._rules_text = "\n\n".join(
            t for t in (_always_rules, _recall_rules) if t
        )
        from ..knowledge.catalog import load_catalog
        self._notes_catalog = load_catalog(self.config)
        from ..codebase_index import ensure_index
        self._codebase_index = ensure_index(self.config)

    def _load_lessons_text(self) -> str:
        """一次性读好 lessons 文本。"""
        from ..memory.lessons import LessonsBlock
        try:
            lb = LessonsBlock(self.config)
            text = lb.read().strip()
        except Exception:
            return ""
        if not text or "还没有教训" in text:
            return ""
        return f"# 你从过去中学到的教训（RefleXion 教训库）\n{text}"

    # ── 事件循环绑定 ────────────────────────────────────────────────

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """绑定事件循环（启动时调用一次）。"""
        self._loop = loop

    # ── Confirm 桥接 ────────────────────────────────────────────────

    def _confirm(self, msg: str, tool_name: str | None = None) -> bool:
        """Confirm 回调：推入 ConfirmRequest 到事件队列，阻塞等待浏览器响应。

        此方法由 agent 线程（chat_turn_stream 内部）调用。
        """
        confirm_id = uuid.uuid4().hex[:8]
        event = threading.Event()
        result = {"approved": False}

        with self._confirm_lock:
            self._pending_confirm = {
                "id": confirm_id,
                "event": event,
                "result": result,
            }

        # 推入 ConfirmRequest 到 asyncio 队列（线程安全）
        if self._loop:
            self._loop.call_soon_threadsafe(
                self.event_queue.put_nowait,
                {
                    "type": "confirm_request",
                    "id": confirm_id,
                    "message": msg,
                    "tool_name": tool_name,
                },
            )

        # 阻塞等待浏览器响应（agent 线程在此暂停）
        event.wait(timeout=_CONFIRM_TIMEOUT_S)

        with self._confirm_lock:
            approved = result["approved"]
            self._pending_confirm = None
        return approved

    def respond_confirm(self, confirm_id: str, approved: bool) -> bool:
        """响应确认请求（由 receiver 协程调用）。"""
        with self._confirm_lock:
            if self._pending_confirm and self._pending_confirm["id"] == confirm_id:
                self._pending_confirm["result"]["approved"] = approved
                self._pending_confirm["event"].set()
                return True
        return False

    # ── 事件序列化 ──────────────────────────────────────────────────

    @staticmethod
    def _serialize_event(event: Any) -> dict[str, Any]:
        """将 EventBus 事件序列化为 JSON 可传输的字典。"""
        if isinstance(event, TurnStart):
            return {"type": "turn_start", "session_id": event.session_id}
        elif isinstance(event, AssistantChunk):
            return {"type": "assistant_chunk", "delta": event.delta}
        elif isinstance(event, ToolCallStart):
            return {
                "type": "tool_call_start",
                "tool_name": event.tool_name,
                "args": event.args,
                "tool_call_id": event.tool_call_id,
            }
        elif isinstance(event, ToolCallEnd):
            return {
                "type": "tool_call_end",
                "tool_name": event.tool_name,
                "args": event.args,
                "is_error": event.is_error,
                "result": str(event.result)[:3000],
                "tool_call_id": event.tool_call_id,
            }
        elif isinstance(event, ToolDenied):
            return {
                "type": "tool_denied",
                "tool_name": event.tool_name,
                "args": event.args,
                "reason": event.reason,
                "tool_call_id": event.tool_call_id,
            }
        elif isinstance(event, TurnEnd):
            return {"type": "turn_end", "usage": event.usage}
        elif isinstance(event, TurnError):
            return {"type": "turn_error", "error": event.error}
        elif isinstance(event, TurnRetrying):
            return {
                "type": "turn_retrying",
                "attempt": event.attempt,
                "max_retries": event.max_retries,
                "wait_seconds": event.wait_seconds,
                "error": error
                if (error := event.error) else "",
            }
        elif isinstance(event, ContextCompacted):
            return {
                "type": "context_compacted",
                "summary": event.summary,
                "retained_message_count": event.retained_message_count,
            }
        return {"type": "unknown_event", "event_repr": repr(event)}

    # ── 对话轮次 ────────────────────────────────────────────────────

    async def start_turn(self, user_input: str) -> None:
        """启动一轮对话，在后台线程运行 agent，事件通过 event_queue 流出。

        此方法由 receiver 协程调用。不阻塞调用者——事件通过 event_queue 异步传递。
        """
        loop = self._loop
        if not loop:
            raise RuntimeError("事件循环未绑定，请先调用 bind_loop")

        bus = EventBus()

        # 订阅 EventBus，将事件推入 asyncio 队列（线程安全）
        def on_event(event: Any) -> None:
            serialized = self._serialize_event(event)
            loop.call_soon_threadsafe(self.event_queue.put_nowait, serialized)

        bus.subscribe(on_event)

        # 构建 deps
        deps = AlfredDeps(
            config=self.config,
            blocks=self.blocks,
            confirm=self._confirm,
            skill_index=self._skill_index,
            lessons_text=self._lessons_text,
            rules_text=self._rules_text,
            notes_catalog=self._notes_catalog,
            codebase_index=self._codebase_index,
        )

        # 在后台线程运行 chat_turn_stream（同步生成器）
        def _run() -> None:
            try:
                for _ in chat_turn_stream(
                    self.agent, deps, self.session, user_input, bus=bus
                ):
                    pass  # 事件已通过 bus 订阅推入队列
            except Exception as exc:
                logger.exception("chat_turn_stream 异常")
                loop.call_soon_threadsafe(
                    self.event_queue.put_nowait,
                    {"type": "turn_error", "error": str(exc)},
                )
            finally:
                loop.call_soon_threadsafe(
                    self.event_queue.put_nowait, {"type": "turn_complete"}
                )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    # ── 模型切换 ────────────────────────────────────────────────────

    def switch_model(self, model_ref: str) -> None:
        """切换模型（重建 agent）。"""
        self.config.resolve(model_ref)
        self.config.models.chat = model_ref
        self.agent = build_agent(self.config, model_ref)

    # ── 会话历史 ────────────────────────────────────────────────────

    def get_messages(self) -> list[dict]:
        """获取当前会话的消息列表（供 REST API 使用）。"""
        return [
            {
                "role": m.role,
                "content": m.content,
                "ts": m.ts,
                "name": m.name,
                "tool_calls": [asdict(tc) for tc in m.tool_calls],
            }
            for m in self.session.messages
        ]

    def get_session_id(self) -> str:
        """获取会话 ID。"""
        return self.session.id
