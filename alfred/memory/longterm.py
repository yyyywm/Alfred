"""长期记忆：通过 MemoryClient 协议封装，支持本地/云端切换。

设计要点：
- 记忆写入路径固定用 config.models.memory_write 的强模型（抽取质量敏感）
- 所有客户端实现同一 MemoryClient 协议，支持多 agent 共享
- 多 agent 共享时通过 user_id 实现租户隔离
- 初始化失败时降级为空实现——记忆系统故障不应让对话崩溃，
  但失败必须可见（init_status），不能静默（借鉴 operator-memory
  的 "Failure Is Visible"：故障留在可检查的地方，而不是吞掉）
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
# 最近一次初始化失败的原因（异常类型: 消息），供 init_status() 暴露给
# CLI / agent——旧实现只有 _init_failed 布尔位，用户聊了很久才发现记忆
# 根本没写入（mem0 Qdrant .lock 残留是最常见原因）。
_init_error: str | None = None

# redirect_stdout 换的是进程全局 sys.stdout：两个写入线程重叠时，后退出者
# 会把 sys.stdout 恢复成对方已关闭的 devnull，主线程下一次 print 即
# ValueError（chat 静默退出、退出码 1 的根因）。加锁串行化写入窗口。
_add_lock = threading.Lock()


def _select_provider(config: Config) -> str:
    global _provider, _init_failed, _init_error
    chosen = config.memory.provider
    if chosen != _provider:
        _provider = chosen
        _user_clients.clear()
        _init_failed = False
        _init_error = None
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
    global _init_failed, _init_error
    try:
        return _make_client(config, user_id)
    except Exception as exc:
        if _is_qdrant_lock_error(exc) and _clear_qdrant_lock(config):
            try:
                return _make_client(config, user_id)
            except Exception as retry_exc:
                _init_failed = True
                _init_error = f"{type(retry_exc).__name__}: {retry_exc}"
                return None
        _init_failed = True
        _init_error = f"{type(exc).__name__}: {exc}"
        return None


def init_status() -> str | None:
    """记忆层初始化失败的原因；未尝试过或初始化成功返回 None。

    失败可见化入口：CLI `/status` 与 memory_search 工具用它把
    "记忆层离线" surfaced 给用户/agent，而不是静默降级为空结果。
    """
    return _init_error


def peek_client(config: Config, user_id: str | None = None) -> MemoryClient | None:
    """只读缓存：已初始化的 client 则返回，未初始化返回 None（不触发构建）。

    供 `/status` 这类诊断视图使用——get_client 是懒加载且首次构建可能
    下载 embedding 模型，诊断命令不应触发重初始化。
    """
    return _user_clients.get(user_id or config.memory.default_user_id)


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
    global _user_clients, _init_failed, _init_error
    _user_clients = {}
    _init_failed = False
    _init_error = None


# 租户 id 一律从 config.memory.default_user_id 读取，这里不再放硬编码副本
# （两处常量会漂移，已发生过：consolidate 写死 USER_ID 而非读 config）。

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
        with _add_lock:
            # utf-8 + replace：窗口内其他线程（如 spinner 刷新）写到 devnull 时
            # 不会因默认 GBK 编码无法表示 braille 字符而 UnicodeEncodeError 崩线程
            with open(os.devnull, "w", encoding="utf-8", errors="replace") as devnull:
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


# ── 事实型记忆写入：去重门禁 ─────────────────────────────────────────
# 借鉴 operator-memory 对 replay 式记忆的批判："过时的决策和它的替代者
# 并排出现，模型自己猜"。mem0 内部的 ADD/UPDATE/DELETE 推断能处理语义级
# 冲突，但 consolidate 反复跑、对话反复提同一事实时，近乎相同的记录会
# 绕过语义阈值不断累积。写入前做一次确定性去重：相似度足够高就跳过，
# 保持"一个事实一份记录"。

# 双信号判重（2026-10-07 真实链路校准，bge-m3）：
# 1. 字符/词集合 Jaccard ≥ 0.85：同语言近重复（「喜欢喝冰咖啡」vs
#    「喜欢喝咖啡」= 0.875 应去重；「周三打球」vs「周四打球」= 0.8 必须保留）。
# 2. 向量分 ≥ 0.85：mem0 抽取会把中文事实改写成英文存储，文本信号失效；
#    向量分实测区分度：同义复述 ≥ 0.91，异事实（美式咖啡/喝茶）≤ 0.73。
# 已知局限：跨语言同义（中文 vs 英译库存）向量分约 0.78，落在两个阈值
# 之间会漏判——由 mem0 内部 UPDATE 推断兜底，不追求完备。
_DUP_SIMILARITY_THRESHOLD = 0.85
_DUP_VECTOR_THRESHOLD = 0.85
_DUP_SEARCH_LIMIT = 5


def _text_signature(text: str) -> set[str]:
    """文本签名：CJK 按字符、拉丁按词，小写归一。

    中文没有空格分词，按字取集合对"用户喜欢咖啡"vs"用户喜欢喝咖啡"
    这类近重复有足够的区分度，且零依赖、确定性、可测试。
    """
    return set(re.findall(r"[a-z0-9]+|[一-鿿]", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_near_duplicate(text_a: str, text_b: str,
                      threshold: float = _DUP_SIMILARITY_THRESHOLD) -> bool:
    """两条记忆文本是否近似重复。"""
    return _jaccard(_text_signature(text_a), _text_signature(text_b)) >= threshold


def _is_dup_neighbor(text: str, neighbor: dict) -> bool:
    """近邻是否构成重复：文本 Jaccard 或向量分任一达到阈值。

    向量分覆盖 mem0 抽取改写（中文事实被存成英文）导致文本信号失效
    的场景；score 缺失（协议不保证）时只靠文本信号。
    """
    existing = neighbor.get("memory") or neighbor.get("text") or ""
    if not existing:
        return False
    if is_near_duplicate(text, existing):
        return True
    score = neighbor.get("score")
    return score is not None and float(score) >= _DUP_VECTOR_THRESHOLD


def add_fact(config: Config, text: str, user_id: str | None = None) -> dict:
    """写入一条事实型记忆，写入前做近重复去重。

    返回 {"status": "added"|"duplicate"|"offline", ...}：
    - duplicate：已存在相似度 ≥ 阈值的记忆，跳过写入（附 existing 文本）
    - offline：记忆层不可用，未写入
    - added：已写入
    """
    uid = user_id or config.memory.default_user_id
    client = get_client(config, uid)
    if client is None:
        return {"status": "offline"}
    try:
        neighbors = client.search(text, limit=_DUP_SEARCH_LIMIT, user_id=uid)
    except Exception:
        neighbors = []  # 检索失败不阻塞写入；mem0 内部推断兜底
    for m in neighbors:
        if _is_dup_neighbor(text, m):
            return {"status": "duplicate",
                    "existing": m.get("memory") or m.get("text")}
    client.add([{"role": "user", "content": text}], user_id=uid)
    return {"status": "added"}
