"""笔记索引管线：增量索引 Markdown 目录。

增量策略：按文件内容 hash 判断变更，变更的文件整篇重索引（先删旧 chunks 再插入）。
索引状态存 data/vectordb/ingest_state.json。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..config import Config
from . import store
from .chunking import chunk_markdown
from .embed import embed_texts

TABLE = "notes"


def _state_path(config: Config) -> Path:
    return config.path(config.paths.vectordb_dir) / "ingest_state.json"


def _load_state(config: Config) -> dict[str, str]:
    p = _state_path(config)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _save_state(config: Config, state: dict[str, str]) -> None:
    p = _state_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def ingest(config: Config, notes_dir: Path, progress=None) -> dict:
    """索引一个笔记目录。返回统计 {added, updated, skipped, chunks}。"""
    notes_dir = notes_dir.expanduser().resolve()
    if not notes_dir.is_dir():
        raise NotADirectoryError(f"笔记目录不存在：{notes_dir}")

    state = _load_state(config)
    new_state = dict(state)
    stats = {"added": 0, "updated": 0, "skipped": 0, "chunks": 0}

    md_files = sorted(notes_dir.rglob("*.md"))
    for f in md_files:
        rel = str(f.relative_to(notes_dir))
        h = _file_hash(f)
        if state.get(rel) == h:
            stats["skipped"] += 1
            continue
        chunks = chunk_markdown(f, root=notes_dir)
        if not chunks:
            new_state[rel] = h
            continue
        vectors = embed_texts(config, [c.text for c in chunks])
        rows = [
            {
                "chunk_id": f"{rel}#{i}",
                "text": c.text,
                "source": rel,
                "heading_path": c.heading_path,
                "vector": vectors[i],
            }
            for i, c in enumerate(chunks)
        ]
        store.upsert_chunks(config, TABLE, rows)
        stats["updated" if rel in state else "added"] += 1
        stats["chunks"] += len(rows)
        new_state[rel] = h
        if progress:
            progress(rel, len(rows))

    # 已删除的笔记：清理索引
    current = {str(f.relative_to(notes_dir)) for f in md_files}
    for rel in list(new_state):
        if rel not in current:
            store.delete_by_source(config, TABLE, rel)
            del new_state[rel]

    _save_state(config, new_state)
    return stats


def search_notes(config: Config, query: str, limit: int = 5) -> list[dict]:
    from .embed import embed_query

    vec = embed_query(config, query)
    return store.search(config, TABLE, vec, limit=limit)
