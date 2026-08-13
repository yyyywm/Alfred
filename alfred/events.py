"""类型安全、线程安全的 Alfred agent 循环事件总线。

参考 pi agent 的事件驱动运行时：生命周期的每个关键步骤都会发出
可观察事件，让 CLI 与未来扩展可以在不侵入循环核心的前提下作出响应。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

# 模块级 logger，用于记录 listener 异常。
logger = logging.getLogger(__name__)


class Event:
    """所有 Alfred 循环事件的基类。"""


@dataclass(frozen=True)
class TurnStart(Event):
    session_id: str
    user_text: str


@dataclass(frozen=True)
class AssistantChunk(Event):
    session_id: str
    delta: str


@dataclass(frozen=True)
class ToolCallStart(Event):
    session_id: str
    tool_name: str
    args: dict[str, Any]
    tool_call_id: str = ""


@dataclass(frozen=True)
class ToolCallEnd(Event):
    session_id: str
    tool_name: str
    args: dict[str, Any]
    result: Any
    is_error: bool = False
    tool_call_id: str = ""


@dataclass(frozen=True)
class ToolDenied(Event):
    session_id: str
    tool_name: str
    args: dict[str, Any]
    reason: str
    tool_call_id: str = ""


@dataclass(frozen=True)
class TurnEnd(Event):
    session_id: str
    usage: dict[str, Any] | None


@dataclass(frozen=True)
class ContextCompacted(Event):
    session_id: str
    summary: str
    retained_message_count: int


@dataclass(frozen=True)
class TurnError(Event):
    session_id: str
    error: str


Listener = Callable[[Event], None]


class EventBus:
    """同步、线程安全的发布/订阅总线。"""

    def __init__(self) -> None:
        # 不存在重入场景，使用普通 Lock 即可。
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def emit(self, event: Event) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event)
            except Exception:
                # listener 之间必须互不影响：记录异常后继续分发。
                logger.exception("Event listener %r failed for %r", listener, event)
                continue
