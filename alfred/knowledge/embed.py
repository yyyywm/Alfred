"""Embedding：Qwen3-Embedding-0.6B 本地运行（sentence-transformers）。

注意：embedding 模型选定后不要换——换模型 = 全量重建索引。
懒加载单例，首次运行需下载约 600MB。
"""

from __future__ import annotations

from functools import lru_cache

from ..config import Config

_model = None
_dim: int | None = None


def get_model(config: Config):
    global _model, _dim
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(config.models.embed.name, device=config.models.embed.device)
        _dim = _model.get_embedding_dimension()
    return _model


def embed_texts(config: Config, texts: list[str]) -> list[list[float]]:
    model = get_model(config)
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_query(config: Config, query: str) -> list[float]:
    # Qwen3-Embedding 对 query 有 instruction 前缀约定
    model = get_model(config)
    vec = model.encode(
        query,
        prompt_name="query",
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vec.tolist()


def dim(config: Config) -> int:
    global _dim
    if _dim is None:
        get_model(config)
    return _dim or 1024
