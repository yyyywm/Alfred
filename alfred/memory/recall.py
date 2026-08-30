"""混合召回：三层记忆召回的统一入口与预算控制。"""

from __future__ import annotations

import math
import time
from datetime import datetime

from ..config import Config
from . import longterm


def _parse_ts(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        # mem0 的 created_at 是无时区的 ISO 格式（2026-08-30T15:16:00），
        # 之前只试带 %z 的格式，导致近因度恒为 0.5——recency 权重实际失效。
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                return datetime.strptime(value, fmt).timestamp()
            except ValueError:
                continue
    return None


def rank_memories(config: Config, items: list[dict]) -> list[dict]:
    budget = config.memory.recall_budget
    half_life = config.memory.recency_half_life_days * 86400
    now = time.time()

    def fused(m: dict) -> float:
        # 用 m.get(...) is None 判断缺省：0.0 是合法的低分，不能被 or 改成 0.5
        rel = float(m["score"]) if m.get("score") is not None else 0.5
        ts = _parse_ts(m.get("created_at") or m.get("updated_at"))
        if ts is None:
            recency = 0.5
        else:
            recency = math.exp(-math.log(2) * max(now - ts, 0) / half_life)
        return 0.7 * rel + 0.3 * recency

    ranked = sorted(items, key=fused, reverse=True)
    return ranked[:budget]


def recall(config: Config, query: str, user_id: str | None = None) -> list[dict]:
    """按需召回长期记忆（已排序 + 截断）。"""
    raw = longterm.search(
        config, query, limit=config.memory.recall_budget * 2, user_id=user_id,
    )
    return rank_memories(config, raw)


def render_for_prompt(memories: list[dict]) -> str:
    if not memories:
        return "（没有召回相关记忆）"
    lines = []
    for m in memories:
        text = m.get("memory") or m.get("text") or str(m)
        lines.append(f"- {text}")
    return "\n".join(lines)