"""Local MemoryClient 实现：mem0 + 本地 Qdrant。

封装 mem0 为 MemoryClient 协议，供 longterm.py 的 get_client() 工厂调用。
当 memory.provider == "local" 时使用此实现。
"""

from __future__ import annotations

import os
from typing import Any

from ..config import Config
from .protocols import MemoryClient


def _build_mem0(config: Config):
    """构造 mem0 实例（mem0 v1.1 配置格式）。"""
    os.environ.setdefault("MEM0_TELEMETRY", "false")
    import logging
    logging.getLogger("mem0").setLevel(logging.ERROR)
    from mem0 import Memory

    _pname, provider, model_name = config.resolve(config.models.memory_write)
    qdrant_path = config.path(config.paths.vectordb_dir) / "qdrant_mem0"

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

    mem_config = {
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


class LocalMemoryClient(MemoryClient):
    """基于 mem0 的本地记忆客户端。"""

    def __init__(self, config: Config, user_id: str) -> None:
        self._config = config
        self._user_id = user_id
        self._mem = _build_mem0(config)

    def add(
        self,
        messages: list[dict[str, str]],
        *,
        user_id: str = "owner",
    ) -> None:
        self._mem.add(messages, user_id=user_id)

    def search(
        self,
        query: str,
        limit: int = 10,
        *,
        user_id: str = "owner",
    ) -> list[dict]:
        try:
            result = self._mem.search(query, filters={"user_id": user_id}, limit=limit)
        except (ValueError, TypeError):
            result = self._mem.search(query, user_id=user_id, limit=limit)
        if isinstance(result, dict):
            return result.get("results", [])
        return result

    def list_all(self, limit: int = 100, *, user_id: str = "owner") -> list[dict]:
        try:
            result = self._mem.get_all(filters={"user_id": user_id})
        except (ValueError, TypeError):
            result = self._mem.get_all(user_id=user_id)
        if isinstance(result, dict):
            items = result.get("results", [])
        else:
            items = result
        return items[:limit]

    def delete(self, memory_id: str) -> bool:
        self._mem.delete(memory_id)
        return True
