"""会话历史：JSONL 归一化持久化 + LLM 原生消息状态。

双层设计：
- 归一化消息（role/content 纯文本）：给人看、给 compaction/consolidate 用，
  剥离 provider 私有格式，跨模型安全
- llm_state（pydantic-ai 原生消息序列化）：供跨会话精确续跑；
  压缩后丢弃，回退为"摘要 + 近期消息"的种子上下文

工具调用数据模型：
- ``ToolCallRecord`` 是 assistant 消息上的非规范化摘要（tool_name/args/result/is_error/tool_call_id），
  用于复盘与上下文呈现，并不替代完整工具结果。
- ``add_tool()`` 可供调用者独立保存完整工具结果为 ``role="tool"`` 消息；但当前 agent 循环
  将完整工具结果保留在 pydantic-ai 的 ``llm_state`` 中，``ToolCallRecord`` 只是归一化消息里的
  一份去规范化摘要。

时间戳等易变信息放消息层（KV-cache 纪律：不进 system prompt 头部）。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import Config

Role = Literal["user", "assistant", "tool"]


@dataclass
class ToolCallRecord:
    """助理消息上附带的工具调用记录（非规范化摘要）。

    完整的工具结果消息通过 ``Session.add_tool()`` 单独持久化为 ``role="tool"`` 消息，
    二者通过 ``tool_call_id`` 关联。
    """

    tool_name: str
    args: dict[str, Any]
    result: str
    is_error: bool = False
    tool_call_id: str | None = None


@dataclass
class Message:
    role: Role
    content: str
    ts: float = field(default_factory=time.time)
    name: str | None = None          # 工具名（role=tool 时）
    tool_call_id: str | None = None
    compacted: bool = False          # 被压缩裁剪过的标记
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


def _message_from_record(record: dict) -> Message:
    """从 JSON 恢复 Message，并把嵌套的工具调用记录转回 dataclass。"""
    record = dict(record)
    record.pop("type", None)
    tool_call_dicts = record.pop("tool_calls", [])
    tool_calls = [ToolCallRecord(**r) for r in tool_call_dicts]
    return Message(tool_calls=tool_calls, **record)


@dataclass
class SessionInfo:
    """list_sessions 返回的会话摘要（含标题元数据）。"""

    id: str
    mtime: float
    msg_count: int
    title: str | None = None       # None = 未设置标题
    title_auto: bool = True        # True=自动概括，False=用户手动设置


class Session:
    """一个会话 = 一个 JSONL 文件。append-only；压缩时整体重写。"""

    def __init__(self, config: Config, session_id: str | None = None):
        self.dir = config.path(config.paths.history_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.id = session_id or uuid.uuid4().hex[:12]
        self.file = self.dir / f"{self.id}.jsonl"
        self.messages: list[Message] = []
        self.llm_state: bytes | None = None  # pydantic-ai 原生消息（JSON bytes）
        if self.file.exists():
            self._load()

    def _load(self) -> None:
        for line in self.file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("type") == "llm_state":
                self.llm_state = record["data"].encode("utf-8")
            else:
                self.messages.append(_message_from_record(record))

    def _write_line(self, record: dict) -> None:
        with self.file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append(self, msg: Message) -> None:
        self.messages.append(msg)
        self._write_line(asdict(msg))

    def add_user(self, content: str) -> None:
        self.append(Message(role="user", content=content))

    def add_assistant(
        self, content: str, tool_calls: list[ToolCallRecord] | None = None
    ) -> None:
        self.append(
            Message(role="assistant", content=content, tool_calls=tool_calls or [])
        )

    def add_tool(self, name: str, content: str, tool_call_id: str | None = None) -> None:
        self.append(Message(role="tool", content=content, name=name, tool_call_id=tool_call_id))

    def set_llm_state(self, state: bytes) -> None:
        self.llm_state = state
        self._write_line({"type": "llm_state", "data": state.decode("utf-8")})

    def rewrite(self) -> None:
        """压缩后整体重写：归一化消息重写，llm_state 作废。"""
        self.llm_state = None
        with self.file.open("w", encoding="utf-8") as f:
            for m in self.messages:
                f.write(json.dumps(asdict(m), ensure_ascii=False) + "\n")

    def transcript(self, max_chars_per_msg: int = 500) -> str:
        """导出可读文本记录（种子上下文 / consolidate 用）。"""
        lines = []
        for m in self.messages:
            content = m.content[:max_chars_per_msg]
            tag = m.role + (f"/{m.name}" if m.name else "")
            lines.append(f"[{tag}] {content}")
        return "\n".join(lines)


def list_sessions(config: Config) -> list[SessionInfo]:
    """返回会话摘要列表（含标题），按最近修改排序。

    排除非会话 JSONL（consolidate 元数据/待审草稿），否则会被下游当成
    会话历史解析而崩溃（drafts 字段不在 Message schema 内）。
    sessions_meta.json 是 .json 后缀，*.jsonl glob 天然不匹配。
    """
    d = config.path(config.paths.history_dir)
    if not d.exists():
        return []
    meta_files = {"consolidate_state.jsonl", "consolidate_pending.jsonl"}
    meta = _load_meta(config)
    rows = []
    for f in d.glob("*.jsonl"):
        if f.name in meta_files:
            continue
        count = 0
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip() and '"type": "llm_state"' not in line:
                count += 1
        entry = meta.get(f.stem) or {}
        rows.append(SessionInfo(
            id=f.stem,
            mtime=f.stat().st_mtime,
            msg_count=count,
            title=entry.get("title"),
            title_auto=entry.get("auto", True),
        ))
    return sorted(rows, key=lambda r: r.mtime, reverse=True)


SESSIONS_META_FILE = "sessions_meta.json"


def _meta_path(config: Config) -> Path:
    return config.path(config.paths.history_dir) / SESSIONS_META_FILE


def _load_meta(config: Config) -> dict:
    """读 sessions_meta.json；文件损坏按空表处理，不影响会话功能。"""
    f = _meta_path(config)
    if not f.exists():
        return {}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logging.getLogger(__name__).warning("sessions_meta.json 损坏，按空表处理: %s", e)
        return {}


def _save_meta(config: Config, meta: dict) -> None:
    f = _meta_path(config)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def set_title(config: Config, session_id: str, title: str, auto: bool) -> None:
    """设置会话标题。auto=True 表示自动概括，False 表示用户手动设置。"""
    meta = _load_meta(config)
    meta[session_id] = {"title": title, "auto": auto, "updated_at": time.time()}
    _save_meta(config, meta)


def get_title(config: Config, session_id: str) -> str | None:
    entry = _load_meta(config).get(session_id)
    return entry["title"] if entry else None


def delete_session(config: Config, session_id: str) -> bool:
    """删除指定会话的历史文件与标题元数据。返回是否删除成功。"""
    safe_id = Path(session_id).name  # 防路径穿越
    f = config.path(config.paths.history_dir) / f"{safe_id}.jsonl"
    if not f.exists():
        return False
    f.unlink()
    meta = _load_meta(config)
    if safe_id in meta:
        del meta[safe_id]
        _save_meta(config, meta)
    return True


def session_preview(config: Config, session_id: str, max_chars: int = 30) -> str | None:
    """无标题会话的回退展示：首条用户消息截断（只读，不写回存储）。"""
    safe_id = Path(session_id).name
    f = config.path(config.paths.history_dir) / f"{safe_id}.jsonl"
    if not f.exists():
        return None
    try:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip() or '"type": "llm_state"' in line:
                continue
            record = json.loads(line)
            if record.get("role") == "user":
                text = record.get("content", "").strip().replace("\n", " ")
                if not text:
                    return None
                return text[:max_chars] + "…" if len(text) > max_chars else text
    except (json.JSONDecodeError, OSError):
        return None
    return None
