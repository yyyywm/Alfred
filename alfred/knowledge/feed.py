"""喂书管线：分段通读 → 提炼思维框架卡片 → 入库校验 → frameworks 表。

设计依据：
- Letta sleep-time 文档消化：后台通读文档，把重要发现沉淀下来
- Voyager：入库校验——提炼出的框架必须结构完整（名称/核心观点/适用场景/来源），
  缺项拒收，防止低质量内容污染框架库
- 重要框架回写 persona 只走"建议 + 人工确认"（防人格漂移）
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic_ai import Agent

from ..config import Config
from ..knowledge import store
from ..knowledge.embed import embed_texts
from ..llm import build_model

TABLE = "frameworks"
SEGMENT_CHARS = 6000  # 每段通读长度

EXTRACT_INSTRUCTIONS = """你是思维框架提炼器。从给定文本中提炼可复用的思维模型/思考框架。

对每个框架输出：
- name：框架名称（简洁、可索引）
- core：核心观点（2-3 句，说清"怎么想"）
- apply：适用场景（什么时候该用它）
- source：来源（书名/章节，从文本中判断）

输出严格 JSON 数组：[{"name":..., "core":..., "apply":..., "source":...}, ...]
没有可提炼的框架就输出 []。宁缺毋滥：只提炼真正有方法论价值的内容，
金句摘抄、泛泛而谈不算框架。"""

REQUIRED_FIELDS = ("name", "core", "apply", "source")


def _validate(card: dict) -> bool:
    """Voyager 式入库校验：四要素缺一不可，且不能是占位空话。"""
    for f in REQUIRED_FIELDS:
        v = str(card.get(f) or "").strip()
        if len(v) < 4:
            return False
    return True


def distill_segment(config: Config, text: str, source_name: str) -> list[dict]:
    agent = Agent(
        build_model(config, config.models.memory_write),
        instructions=EXTRACT_INSTRUCTIONS,
    )
    result = agent.run_sync(f"来源：《{source_name}》\n\n文本：\n{text}")
    out = result.output.strip()
    if out.startswith("```"):
        out = out.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        cards = json.loads(out)
    except json.JSONDecodeError:
        return []
    return [c for c in cards if isinstance(c, dict) and _validate(c)]


def feed(config: Config, file: Path, progress=None) -> dict:
    """消化一个文件（书/文章），提炼框架卡片入库。返回统计。"""
    file = file.expanduser().resolve()
    if not file.is_file():
        raise FileNotFoundError(file)

    text = file.read_text(encoding="utf-8")
    # 去 markdown frontmatter，按长度分段（尽量在标题边界断）
    text = re.sub(r"\A---\s*\n.*?\n---\s*\n?", "", text, flags=re.DOTALL)
    segments = []
    while text:
        seg, text = text[:SEGMENT_CHARS], text[SEGMENT_CHARS:]
        if text:  # 尽量在换行处断开
            cut = seg.rfind("\n\n")
            if cut > SEGMENT_CHARS // 2:
                seg, text = seg[:cut], seg[cut:] + text
        segments.append(seg)

    all_cards: list[dict] = []
    for i, seg in enumerate(segments):
        cards = distill_segment(config, seg, file.stem)
        all_cards.extend(cards)
        if progress:
            progress(i + 1, len(segments), len(cards))

    if all_cards:
        vectors = embed_texts(
            config, [f"{c['name']}：{c['core']}（适用：{c['apply']}）" for c in all_cards]
        )
        rows = [
            {
                "chunk_id": f"{file.stem}#{i}",
                "name": c["name"],
                "text": f"【{c['name']}】{c['core']}\n适用场景：{c['apply']}\n来源：{c['source']}",
                "source": file.name,
                "vector": vectors[i],
            }
            for i, c in enumerate(all_cards)
        ]
        store.upsert_chunks(config, TABLE, rows)

    return {"segments": len(segments), "frameworks": len(all_cards),
            "names": [c["name"] for c in all_cards]}


def search_frameworks(config: Config, query: str, limit: int = 3) -> list[dict]:
    from ..knowledge.embed import embed_query

    vec = embed_query(config, query)
    return store.search(config, TABLE, vec, limit=limit)
