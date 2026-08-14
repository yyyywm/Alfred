"""长期记忆：mem0 开源版封装。

设计要点：
- 记忆写入路径固定用 config.models.memory_write 的强模型（抽取质量敏感）
- 向量库用本地嵌入式 Qdrant，数据不出本机
- mem0 v3 开源版是 ADD-only（只增不改），整合/去矛盾由 consolidate.py 补足
- 初始化失败时降级为空实现——记忆系统故障不应让对话崩溃
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..config import Config

_mem = None
_init_failed = False


def _build_mem0(config: Config):
    import os

    os.environ.setdefault("MEM0_TELEMETRY", "false")  # 私人数据不上报
    # mem0 的 keyword/BM25 等可选组件缺失时会打印 warning，屏蔽掉以免污染终端
    logging.getLogger("mem0").setLevel(logging.ERROR)
    from mem0 import Memory

    _pname, provider, model_name = config.resolve(config.models.memory_write)
    qdrant_path = config.path(config.paths.vectordb_dir) / "qdrant_mem0"

    # mem0 的 LLM 配置跟随我们的 provider 类型（Anthropic 兼容端点不能用 openai provider）
    if provider.type == "anthropic":
        llm_cfg: dict[str, Any] = {
            "provider": "anthropic",
            "config": {
                "model": model_name,
                "api_key": provider.api_key(),
                "anthropic_base_url": provider.base_url,
                "temperature": 0.1,
            },
        }
    else:
        llm_cfg = {
            "provider": "openai",
            "config": {
                "model": model_name,
                "openai_base_url": provider.base_url or "https://api.openai.com/v1",
                "api_key": provider.api_key() or "not-needed",
                "temperature": 0.1,
            },
        }

    embed_cfg = config.models.embed
    if embed_cfg.provider == "openai_compat":
        embedder_cfg: dict[str, Any] = {
            "provider": "openai",
            "config": {
                "model": embed_cfg.name,
                "api_key": embed_cfg.resolve_api_key() or "",
                "openai_base_url": embed_cfg.base_url,
            },
        }
    elif embed_cfg.provider == "local":
        embedder_cfg = {
            "provider": "huggingface",
            "config": {"model": embed_cfg.name},
        }
    else:
        raise ValueError(f"不支持的 embedding provider: {embed_cfg.provider}")

    mem_config: dict[str, Any] = {
        "version": "v1.1",
        "llm": llm_cfg,
        "embedder": embedder_cfg,
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "alfred_memories",
                "path": str(qdrant_path),
                "embedding_model_dims": embed_cfg.dims or 1024,
            },
        },
    }
    return Memory.from_config(mem_config)


def _is_qdrant_lock_error(exc: Exception) -> bool:
    """判断异常是否为 Qdrant 本地存储锁冲突。"""
    text = str(exc).lower()
    return "already accessed by another instance" in text or "alreadylocked" in text


def _clear_qdrant_lock(config: Config) -> bool:
    """删除残留的 Qdrant .lock 文件，返回是否执行了清理。"""
    qdrant_path = config.path(config.paths.vectordb_dir) / "qdrant_mem0" / ".lock"
    if qdrant_path.is_file():
        try:
            qdrant_path.unlink()
            return True
        except OSError:
            return False
    return False


def get_memory(config: Config):
    """懒加载单例；失败返回 None（降级）。

    若因 Qdrant 残留锁文件导致初始化失败，自动清理后重试一次。
    """
    global _mem, _init_failed
    if _mem is None and not _init_failed:
        try:
            _mem = _build_mem0(config)
        except Exception as exc:
            if _is_qdrant_lock_error(exc) and _clear_qdrant_lock(config):
                try:
                    _mem = _build_mem0(config)
                except Exception:
                    _init_failed = True
            else:
                _init_failed = True
    return _mem


USER_ID = "owner"  # 单用户私人管家，固定 user id


def add_async(config: Config, user_msg: str, assistant_msg: str) -> None:
    """对话轮结束后台线程抽取记忆（hot path 零延迟）。

    后台线程内屏蔽 stdout/stderr，防止 mem0 或依赖库意外输出污染终端，
    避免与 prompt_toolkit 的输入显示冲突。
    """

    def _run():
        import contextlib
        import os

        mem = get_memory(config)
        if mem is None:
            return
        with open(os.devnull, "w") as devnull:
            with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
                try:
                    mem.add(
                        [
                            {"role": "user", "content": user_msg},
                            {"role": "assistant", "content": assistant_msg},
                        ],
                        user_id=USER_ID,
                    )
                except Exception:
                    pass

    threading.Thread(target=_run, daemon=True).start()


def search(config: Config, query: str, limit: int = 10) -> list[dict]:
    """召回相关记忆。返回 [{id, memory, score, created_at}, ...]"""
    mem = get_memory(config)
    if mem is None:
        return []
    try:
        # mem0 v2：实体参数走 filters；旧版为顶层 user_id
        try:
            result = mem.search(query, filters={"user_id": USER_ID}, limit=limit)
        except (ValueError, TypeError):
            result = mem.search(query, user_id=USER_ID, limit=limit)
        return result.get("results", []) if isinstance(result, dict) else result
    except Exception:
        return []


def list_all(config: Config, limit: int = 100) -> list[dict]:
    mem = get_memory(config)
    if mem is None:
        return []
    try:
        try:
            result = mem.get_all(filters={"user_id": USER_ID})
        except (ValueError, TypeError):
            result = mem.get_all(user_id=USER_ID)
        items = result.get("results", []) if isinstance(result, dict) else result
        return items[:limit]
    except Exception:
        return []


def delete(config: Config, memory_id: str) -> bool:
    mem = get_memory(config)
    if mem is None:
        return False
    try:
        mem.delete(memory_id)
        return True
    except Exception:
        return False
