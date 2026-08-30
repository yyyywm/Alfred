"""LanceDB 向量存储：notes / frameworks / episodes 三张表。

知识层与记忆层严格分离：
- notes：用户笔记的 RAG 索引（知识层）
- frameworks：喂书提炼的思维框架卡片（知识层）
- episodes：成功案例四元组（记忆层的情景记忆，见 memory/episodic.py）
"""

from __future__ import annotations

from pathlib import Path

import lancedb

from ..config import Config

_db = None


def get_db(config: Config):
    global _db
    if _db is None:
        path = config.path(config.paths.vectordb_dir)
        path.mkdir(parents=True, exist_ok=True)
        _db = lancedb.connect(str(path))
    return _db


def _table_names(db) -> set[str]:
    """返回库中所有表名（set）。

    lancedb >= 0.33 的 list_tables() 返回 ListTablesResponse 对象而非字符串列表，
    直接 `in` 判断会永远为 False。这里统一归一化为纯表名集合。
    """
    tables = db.list_tables()
    if hasattr(tables, "tables"):
        tables = tables.tables
    return set(tables)


def _open_or_create(db, table: str, schema_rows: list[dict]):
    """表不存在时用首行数据创建；空数据则创建带一行占位的表再删除。"""
    if table in _table_names(db):
        return db.open_table(table)
    if not schema_rows:
        raise ValueError("首次创建表需要至少一行数据")
    return db.create_table(table, schema_rows)


def upsert_chunks(config: Config, table: str, rows: list[dict], key: str = "chunk_id") -> int:
    """按 key 去重 upsert（先删后插）。rows 需含 vector 字段。"""
    if not rows:
        return 0
    db = get_db(config)
    keys = [r[key] for r in rows]
    if table in _table_names(db):
        t = db.open_table(table)
        quoted = ",".join(f"'{k}'" for k in keys)
        t.delete(f"{key} IN ({quoted})")
        t.add(rows)
    else:
        db.create_table(table, rows)
    return len(rows)


def search(config: Config, table: str, vector: list[float], limit: int = 5,
           where: str | None = None) -> list[dict]:
    db = get_db(config)
    if table not in _table_names(db):
        return []
    t = db.open_table(table)
    q = t.search(vector).limit(limit)
    if where:
        q = q.where(where)
    return q.to_list()


def delete_by_source(config: Config, table: str, source: str) -> None:
    db = get_db(config)
    if table in _table_names(db):
        db.open_table(table).delete(f"source = '{source}'")
