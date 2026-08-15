from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MemoryClient(Protocol):
    """长期记忆客户端协议。

    支持本地嵌入（sentence-transformers）和云端 provider。
    多 agent 共享同一记忆基础设施时，通过 user_id 实现租户隔离。
    """

    def add(
        self,
        messages: list[dict[str, str]],
        *,
        user_id: str = "owner",
    ) -> None:
        """添加记忆条目。messages 为 [{"role": ..., "content": ...}, ...]。"""

    def search(
        self,
        query: str,
        limit: int = 10,
        *,
        user_id: str = "owner",
    ) -> list[dict]:
        """召回相关记忆。"""

    def list_all(self, limit: int = 100, *, user_id: str = "owner") -> list[dict]:
        """列出所有记忆。"""

    def delete(self, memory_id: str) -> bool:
        """按 id 删除记忆。"""


@runtime_checkable
class EmbeddingClient(Protocol):
    """嵌入客户端协议。本地或云端均可。"""

    def encode(
        self,
        texts: list[str],
        *,
        is_query: bool = False,
    ) -> list[list[float]]:
        """把文本编码为向量。"""

    @property
    def dims(self) -> int:
        """向量维度。"""
