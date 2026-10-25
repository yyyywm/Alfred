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

内存优化：
  - 静态数据（技能索引、教训、规则、笔记目录、代码库索引）由 AppContext 加载一次，
    通过构造参数注入，避免每个 WebSocket 连接重复 I/O。
  - MemoryBlocks 和 LessonsBlock 实例从 AppContext 共享。
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

logger = logging.getLogger(__name__)

_CONFIRM_TIMEOUT_S = 300


class WebSession:
    """一个 Web 会话 = 一个浏览器标签页 + 一个 JSONL 会话文件。

    生命周期：
    1. 创建实例（绑定 session_id 和 config，静态数据从 AppContext 注入）
    2. start_turn(user_input) — 启动后台线程运行对话轮次
    3. event_queue — 异步事件队列，供 sender 协程读取
    4. respond_confirm(id, approved) — 响应确认请求
    5. get_messages() — 获取会话历史（REST API 用）
    """

    def __init__(
        self,
        config: Config,
        session_id: str | None = None,
        *,
        skill_index: str = "",
        lessons_text: str = "",
        rules_text: str = "",
        notes_catalog: str = "",
        codebase_index: str = "",
        blocks: MemoryBlocks | None = None,
        lessons: Any = None,
    ):
        self.config = config
        self.session = Session(config, session_id=session_id)
        self.agent = build_agent(config)
        self.blocks = blocks or MemoryBlocks(config)

        # 共享的静态数据（从 AppContext 注入，避免重复加载）
        self._skill_index = skill_index
        self._lessons_text = lessons_text
        self._rules_text = rules_text
        self._notes_catalog = notes_catalog
        self._codebase_index = codebase_index
        self._lessons = lessons

        # 异步事件队列
        self.event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        # confirm 桥接
        self._pending_confirm: dict[str, Any] | None = None
        self._confirm_lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ── Confirm 桥接 ──

    def _confirm(self, msg: str, tool_name: str | None = None) -> bool:
        """确认回调（同步，被 agent 线程调用）。阻塞直到浏览器响应或超时。"""
        confirm_id = uuid.uuid4().hex[:8]
        event = threading.Event()
        with self._confirm_lock:
            self._pending_confirm = {"id": confirm_id, "event": event}

        # 推送确认请求到事件队列
        self._emit_to_queue({
            "type": "confirm_request",
            "id": confirm_id,
            "message": msg,
            "tool_name": tool_name,
        })

        try:
            approved = event.wait(timeout=_CONFIRM_TIMEOUT_S)
            if not approved:
                logger.warning("确认超时（%ds），自动拒绝", _CONFIRM_TIMEOUT_S)
                return False
            return True
        finally:
            with self._confirm_lock:
                if self._pending_confirm and self._pending_confirm["id"] == confirm_id:
                    self._pending_confirm = None

    def respond_confirm(self, confirm_id: str, approved: bool) -> bool:
        """响应确认请求（异步，被 WebSocket receiver 调用）。线程安全。"""
        with self._confirm_lock:
            if not self._pending_confirm:
                return False
            if self._pending_confirm["id"] != confirm_id:
                return False
            self._pending_confirm["event"].set()
            return True

    def _emit_to_queue(self, event: dict[str, Any]) -> None:
        """线程安全地推送事件到 asyncio.Queue。"""
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(self._async_emit(event), self._loop)
        elif self._loop is None:
            try:
                self.event_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _async_emit(self, event: dict[str, Any]) -> None:
        await self.event_queue.put(event)

    # ── 事件序列化 ──

    @staticmethod
    def _serialize_event(event: Any) -> dict[str, Any] | None:
        """将 EventBus 事件序列化为可 JSON 传输的字典。"""
        if isinstance(event, TurnStart):
            return {"type": "turn_start", "session_id": event.session_id}
        if isinstance(event, AssistantChunk):
            return {"type": "assistant_chunk", "delta": event.delta}
        if isinstance(event, ToolCallStart):
            return {
                "type": "tool_call_start",
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "args": event.args,
            }
        if isinstance(event, ToolCallEnd):
            return {
                "type": "tool_call_end",
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "result": event.result[:3000] if len(event.result) > 3000 else event.result,
                "is_error": event.is_error,
            }
        if isinstance(event, ToolDenied):
            return {
                "type": "tool_denied",
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "reason": event.reason,
            }
        if isinstance(event, TurnEnd):
            return {"type": "turn_end", "usage": event.usage}
        if isinstance(event, TurnError):
            return {"type": "turn_error", "error": str(event.error)}
        if isinstance(event, TurnRetrying):
            return {
                "type": "turn_retrying",
                "attempt": event.attempt,
                "max_retries": event.max_retries,
                "wait_seconds": event.wait_seconds,
            }
        if isinstance(event, ContextCompacted):
            return {
                "type": "context_compacted",
                "summary": event.summary,
                "retained_message_count": event.retained_message_count,
            }
        return {"type": "unknown_event", "event_type": type(event).__name__}

    def _on_event(self, event: Any) -> None:
        """EventBus 订阅回调：将事件序列化后放入队列。"""
        serialized = self._serialize_event(event)
        if serialized:
            self._emit_to_queue(serialized)

    # ── 对话轮次 ──

    def start_turn(self, user_input: str) -> None:
        """启动一个对话轮次（后台线程）。"""

        def _run_turn():
            bus = EventBus()
            bus.subscribe(self._on_event)

            deps = AlfredDeps(
                config=self.config,
                blocks=self.blocks,
                confirm=self._confirm,
                last_recalled=[],
                session_id=self.session.id,
                bus=bus,
                tool_call_count=0,
                skill_index=self._skill_index,
                lessons_text=self._lessons_text,
                rules_text=self._rules_text,
                notes_catalog=self._notes_catalog,
                codebase_index=self._codebase_index,
            )

            try:
                chat_turn_stream(
                    self.agent,
                    deps,
                    self.session,
                    user_input,
                )
            except Exception as e:
                logger.exception("对话轮次异常")
                self._emit_to_queue({
                    "type": "turn_error",
                    "error": str(e),
                })

        thread = threading.Thread(target=_run_turn, daemon=True, name=f"web-turn-{self.session.id}")
        thread.start()

    # ── 模型切换 ──

    def switch_model(self, model_ref: str) -> None:
        """切换模型（重建 agent）。"""
        self.config.resolve(model_ref)
        self.config.models.chat = model_ref
        self.agent = build_agent(self.config, model_ref)

    # ── 会话历史 ──

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
        return self.session.id
