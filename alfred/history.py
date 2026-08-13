"""会话历史：JSONL 归一化持久化 + LLM 原生消息状态。

双层设计：
- 归一化消息（role/content 纯文本）：给人看、给 compaction/consolidate 用，
  剥离 provider 私有格式，跨模型安全
- llm_state（pydantic-ai 原生消息序列化）：供跨会话精确续跑；
  压缩后丢弃，回退为"摘要 + 近期消息"的种子上下文

时间戳等易变信息放消息层（KV-cache 纪律：不进 system prompt 头部）。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import Config

Role = Literal["user", "assistant", "tool"]


@dataclass
class ToolCallRecord:
    """助理消息上附带的工具调用记录，用于后续复盘与上下文呈现。"""

    tool_name: str
    args: dict
    result: str
    is_error: bool = False


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


def list_sessions(config: Config) -> list[tuple[str, float, int]]:
    """返回 [(session_id, mtime, msg_count)]，按最近修改排序。"""
    d = config.path(config.paths.history_dir)
    if not d.exists():
        return []
    rows = []
    for f in d.glob("*.jsonl"):
        count = 0
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip() and '"type": "llm_state"' not in line:
                count += 1
        rows.append((f.stem, f.stat().st_mtime, count))
    return sorted(rows, key=lambda r: r[1], reverse=True)
