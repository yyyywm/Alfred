"""长期记忆：通过 MemoryClient 协议封装，支持本地/云端切换。

设计要点：
- 记忆写入路径固定用 config.models.memory_write 的强模型（抽取质量敏感）
- 所有客户端实现同一 MemoryClient 协议，支持多 agent 共享
- 多 agent 共享时通过 user_id 实现租户隔离
- 初始化失败时降级为空实现——记忆系统故障不应让对话崩溃
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime
from typing import Any

from ..config import Config
from .protocols import MemoryClient

_user_clients: dict[str, MemoryClient] = {}
_provider = "local"
_init_failed = False


def _select_provider(config: Config) -> str:
    global _provider
    chosen = config.memory.provider
    if chosen != _provider:
        _provider = chosen
        _user_clients.clear()
    return chosen


def _is_qdrant_lock_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "already accessed by another instance" in text or "alreadylocked" in text


def _clear_qdrant_lock(config: Config) -> bool:
    qdrant_path = config.path(config.paths.vectordb_dir) / "qdrant_mem0" / ".lock"
    if qdrant_path.is_file():
        try:
            qdrant_path.unlink()
            return True
        except OSError:
            return False
    return False


def _make_client(config: Config, user_id: str) -> MemoryClient:
    provider = _select_provider(config)
    if provider == "local":
        from .local import LocalMemoryClient
        return LocalMemoryClient(config, user_id)
    raise ValueError(f"不支持的记忆 provider: {provider}")


def _new_client_safe(config: Config, user_id: str) -> MemoryClient | None:
    try:
        return _make_client(config, user_id)
    except Exception as exc:
        if _is_qdrant_lock_error(exc) and _clear_qdrant_lock(config):
            try:
                return _make_client(config, user_id)
            except Exception:
                pass
        global _init_failed
        _init_failed = True
        return None


def get_client(config: Config, user_id: str | None = None) -> MemoryClient | None:
    """懒加载用户级 client；失败返回 None（降级）。"""
    uid = user_id or config.memory.default_user_id
    if uid not in _user_clients:
        client = _new_client_safe(config, uid)
        _user_clients[uid] = client
    return _user_clients[uid]


def get_memory(config: Config, user_id: str | None = None) -> MemoryClient | None:
    """向后兼容：与 get_client 相同。"""
    return get_client(config, user_id)


def reset_clients() -> None:
    """测试/切换 provider 时清理缓存。"""
    global _user_clients
    _user_clients = {}


USER_ID = "owner"

_MESSAGE_MIN_CHARS = 20
_TRIVIAL_USER_PATTERNS = (
    "^是$", "^对$", "^嗯", "^好$", "^好的$", "^ok", "^ok了",
    "^谢谢", "^thanks", "^yeah", "^yep", "^nope", "^哈哈", "^haha", "^hehe",
)
_TRIVIAL_ASSISTANT_PATTERNS = (
    "^好", "^收到", "^明白了", "^知道了", "^好的", "^ok", "^嗯",
)


def _is_trivial(msg: str, patterns: tuple[str, ...]) -> bool:
    stripped = msg.strip()
    if not stripped or len(stripped) < _MESSAGE_MIN_CHARS:
        return True
    lowered = stripped.lower()
    return any(re.match(p, lowered) for p in patterns)


def _should_extract(user_msg: str, assistant_msg: str) -> bool:
    return (
        not _is_trivial(user_msg, _TRIVIAL_USER_PATTERNS)
        and not _is_trivial(assistant_msg, _TRIVIAL_ASSISTANT_PATTERNS)
    )


def add_async(
    config: Config, user_msg: str, assistant_msg: str, user_id: str | None = None,
) -> None:
    """对话轮结束后台线程抽取记忆。

    对齐 LycheeMemory V2 (2608.12990) 的段级批处理思想：
    每条消息附带 session 元信息，让 mem0 的 LLM 有更多上下文判断
    哪些内容值得沉淀。同时把静默吞掉的错误改为 warning 日志，
    让 mem0 故障可见。
    """
    if not _should_extract(user_msg, assistant_msg):
        return

    # 把会话上下文作为 metadata 传给 mem0，帮助它区分用户事实 vs agent 自我认知
    metadata = {
        "session": getattr(config, "_current_session", "unknown"),
        "ts": datetime.now().isoformat(),
        "role_types": "user_assistant_pair",
    }

    def _run():
        import contextlib
        import os

        client = get_client(config, user_id)
        if client is None:
            return
        with open(os.devnull, "w") as devnull:
            with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
                try:
                    client.add(
                        [
                            {"role": "user", "content": user_msg},
                            {"role": "assistant", "content": assistant_msg},
                        ],
                        user_id=user_id or config.memory.default_user_id,
                        metadata=metadata,
                    )
                except Exception:
                    logging.getLogger(__name__).warning(
                        "mem0 记忆写入失败（用户消息: %s）", user_msg[:80]
                    )

    threading.Thread(target=_run, daemon=True).start()


def search(
    config: Config, query: str, limit: int = 10, user_id: str | None = None,
) -> list[dict]:
    """召回相关记忆。"""
    client = get_client(config, user_id)
    if client is None:
        return []
    try:
        return client.search(
            query, limit=limit, user_id=user_id or config.memory.default_user_id,
        )
    except Exception:
        return []


def list_all(config: Config, limit: int = 100, user_id: str | None = None) -> list[dict]:
    client = get_client(config, user_id)
    if client is None:
        return []
    try:
        return client.list_all(
            limit=limit, user_id=user_id or config.memory.default_user_id,
        )
    except Exception:
        return []


def delete(config: Config, memory_id: str, user_id: str | None = None) -> bool:
    client = get_client(config, user_id)
    if client is None:
        return False
    try:
        return client.delete(memory_id, user_id=user_id or config.memory.default_user_id)
    except Exception:
        return False
