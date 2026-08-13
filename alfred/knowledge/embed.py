"""Embedding 抽象层。

支持两种 provider：
- local：本地 sentence-transformers 运行（默认 Qwen3-Embedding-0.6B）
- openai_compat：任意 OpenAI 兼容 embedding API（DeepSeek / OpenAI / 月之暗面等）

注意：embedding 模型选定后不要换——换模型 = 全量重建索引。
"""

from __future__ import annotations

import json
import os
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from ..config import Config, EmbedConfig


class Embedder(ABC):
    """统一 embedding 接口。"""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    @abstractmethod
    def dim(self) -> int:
        ...


class LocalEmbedder(Embedder):
    """基于 sentence-transformers 的本地模型。"""

    def __init__(self, config: Config):
        from sentence_transformers import SentenceTransformer

        cfg = config.models.embed
        if cfg.hf_endpoint:
            os.environ["HF_ENDPOINT"] = cfg.hf_endpoint

        model_name_or_path = cfg.local_dir or cfg.name
        self._model = SentenceTransformer(model_name_or_path, device=cfg.device)
        self._dim = self._model.get_embedding_dimension()

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]

    def embed_query(self, query: str) -> list[float]:
        # Qwen3-Embedding 对 query 有 instruction 前缀约定
        vec = self._model.encode(
            query,
            prompt_name="query",
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vec.tolist()

    def dim(self) -> int:
        return self._dim


class OpenAICompatEmbedder(Embedder):
    """任意 OpenAI 兼容 embedding API。"""

    def __init__(self, config: Config):
        cfg = config.models.embed
        if cfg.provider != "openai_compat":
            raise ValueError("OpenAICompatEmbedder 需要 provider='openai_compat'")
        if not cfg.base_url:
            raise ValueError("embedding provider='openai_compat' 需要设置 base_url")
        self._base_url = cfg.base_url.rstrip("/")
        self._model = cfg.name
        self._api_key = cfg.resolve_api_key() or ""
        self._batch_size = max(1, cfg.batch_size)
        self._dim: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), self._batch_size):
            batch = texts[i : i + self._batch_size]
            results.extend(self._call_api(batch))
        return results

    def embed_query(self, query: str) -> list[float]:
        return self.embed([query])[0]

    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_query("test"))
        return self._dim

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, Any] = {
            "input": texts,
            "model": self._model,
            "encoding_format": "float",
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        req = urllib.request.Request(
            f"{self._base_url}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"embedding API 请求失败: {exc.code} {body}") from exc

        items = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [item["embedding"] for item in items]


_cached_embed_cfg: EmbedConfig | None = None
_cached_embedder: Embedder | None = None


def _get_embedder(config: Config) -> Embedder:
    global _cached_embed_cfg, _cached_embedder
    cfg = config.models.embed
    if _cached_embedder is None or _cached_embed_cfg != cfg:
        if cfg.provider == "local":
            _cached_embedder = LocalEmbedder(config)
        elif cfg.provider == "openai_compat":
            _cached_embedder = OpenAICompatEmbedder(config)
        else:
            raise ValueError(f"不支持的 embedding provider: {cfg.provider}")
        _cached_embed_cfg = cfg
    return _cached_embedder


def embed_texts(config: Config, texts: list[str]) -> list[list[float]]:
    return _get_embedder(config).embed(texts)


def embed_query(config: Config, query: str) -> list[float]:
    embedder = _get_embedder(config)
    if isinstance(embedder, LocalEmbedder):
        return embedder.embed_query(query)
    return embedder.embed_query(query)


def dim(config: Config) -> int:
    return _get_embedder(config).dim()
