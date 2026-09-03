# -*- coding: utf-8 -*-
"""embedding 模型切换后的向量重嵌入（无损、离线运行）。

背景：bge-large-zh → bge-m3（同为 1024 维）。同维度切换不会报错，
但新旧向量处于不同语义空间，跨边界召回 = 噪声（见 docs/plans P1）。

本脚本把旧向量原地重嵌入为新模型空间，数据零丢失：
  - episodes.lance：按 text_for_embedding() 同款文本重算 vector
  - qdrant mem0：按 payload["data"] 重算 vector，同 id 原位 upsert
  - notes.lance：跳过（2026-09-03 起已是 bge-m3 索引；若存疑请删库重跑 alfred ingest）

!! 必须在 Alfred 完全关闭后运行（qdrant 本地存储有进程独占锁） !!
用法: python scripts/reembed_vectors.py
"""
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from alfred.config import load_config
from alfred.knowledge.embed import embed_texts

cfg = load_config()
VDB = cfg.path(cfg.paths.vectordb_dir)


def reembed_episodes():
    import lancedb
    db = lancedb.connect(str(VDB))
    if "episodes" not in db.list_tables():
        print("[skip] episodes 表不存在")
        return
    t = db.open_table("episodes")
    rows = t.to_pandas().to_dict("records")
    if not rows:
        print("[skip] episodes 为空")
        return
    texts = [f"场景：{r['situation']}\n思路：{r['thoughts']}" for r in rows]
    vecs = embed_texts(cfg, texts)
    for r, v in zip(rows, vecs):
        r["vector"] = v
    db.create_table("episodes", rows, mode="overwrite")
    print(f"[OK] episodes 重嵌入 {len(rows)} 条")


def reembed_mem0():
    from qdrant_client import QdrantClient
    from qdrant_client.models import PointStruct
    qc = QdrantClient(path=str(VDB / "qdrant_mem0"))
    names = [c.name for c in qc.get_collections().collections]
    if not names:
        print("[skip] qdrant 无集合")
        return
    for name in names:
        offset, total = None, 0
        while True:
            pts, offset = qc.scroll(name, limit=100, offset=offset,
                                    with_payload=True, with_vectors=False)
            batch = [p for p in pts if p.payload and p.payload.get("data")]
            if batch:
                vecs = embed_texts(cfg, [p.payload["data"] for p in batch])
                qc.upsert(name, [PointStruct(id=p.id, vector=v, payload=p.payload)
                                 for p, v in zip(batch, vecs)])
                total += len(batch)
            if offset is None:
                break
        print(f"[OK] mem0 集合 {name} 重嵌入 {total} 条")


if __name__ == "__main__":
    reembed_episodes()
    reembed_mem0()
    print("完成。新旧向量现已统一在同一语义空间。")
