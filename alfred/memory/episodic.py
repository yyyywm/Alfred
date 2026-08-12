"""情景记忆：成功案例四元组库。

设计依据（LangMem 记忆类型学）：情景记忆存"成功交互的结构化案例"
（场景/思路/行动/结果），供 few-shot 召回——这是多数个人 agent 漏掉的一层。
存储在 LanceDB episodes 表（与笔记知识层共用引擎，语义上属于记忆层）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

from ..config import Config
from ..knowledge import store
from ..knowledge.embed import embed_query, embed_texts

TABLE = "episodes"


@dataclass
class Episode:
    situation: str   # 场景：当时要做什么
    thoughts: str    # 思路：怎么分析的
    action: str      # 行动：具体做了什么
    result: str      # 结果：效果如何
    ts: float = 0.0
    episode_id: str = ""

    def text_for_embedding(self) -> str:
        return f"场景：{self.situation}\n思路：{self.thoughts}"


def save_episode(config: Config, ep: Episode) -> str:
    ep.ts = ep.ts or time.time()
    ep.episode_id = ep.episode_id or uuid.uuid4().hex[:12]
    vec = embed_texts(config, [ep.text_for_embedding()])[0]
    store.upsert_chunks(config, TABLE, [{
        "chunk_id": ep.episode_id,
        "situation": ep.situation,
        "thoughts": ep.thoughts,
        "action": ep.action,
        "result": ep.result,
        "ts": ep.ts,
        "vector": vec,
    }])
    return ep.episode_id


def search_episodes(config: Config, query: str, limit: int = 3) -> list[dict]:
    vec = embed_query(config, query)
    return store.search(config, TABLE, vec, limit=limit)
