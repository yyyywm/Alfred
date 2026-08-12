"""Sleep-time 整理：复盘 → 三类草稿 → 用户确认 → 入库（git 版本化）。

设计依据：
- Letta sleep-time compute：后台用强模型把原始对话转化为学习后的记忆
- Devin Knowledge Suggestions：从反馈中沉淀知识，但生成的是"建议草稿"，
  由用户确认——不做全自动无确认的自我改写
- 产物三类：长期记忆条目（mem0）/ 规则修订（rules/）/ human block 更新建议
- mem0 v3 开源版 ADD-only 的整合缺口在此补足：复盘时对矛盾记忆给出处理建议
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic_ai import Agent

from ..config import Config
from ..history import list_sessions
from ..llm import build_model
from . import longterm
from .blocks import MemoryBlocks

CONSOLIDATE_INSTRUCTIONS = """你是私人管家的"睡眠整理"模块。你的任务是复盘管家与用户的近期对话，
提炼出值得长期保留的内容，产出结构化的整理草稿。

输出严格的 JSON（不要输出任何其他内容）：
{
  "memory_entries": ["值得写入长期记忆的事实，每条一句，用第三人称描述用户", ...],
  "human_block_update": "若对用户的整体认知有变化，给出 human 块的完整新内容；无变化则为 null",
  "rule_suggestions": [{"name": "规则名", "content": "规则正文", "reason": "为什么建议"}],  // 用户反复纠正管家的行为模式才提
  "stale_memories": ["与最新信息矛盾的已有记忆原文"]  // 供用户确认后删除
}

提炼标准：
- 只保留长期有效的事实（偏好、经历、决策、目标、关系、习惯），日常琐事不要
- 用户明确纠正过管家的地方优先沉淀
- human_block_update 要压缩到高信号画像，不是流水账
"""


def _recent_transcripts(config: Config, days: int = 3, max_sessions: int = 5) -> str:
    """取最近几天的会话文本记录。"""
    from ..history import Session

    cutoff = datetime.now().timestamp() - days * 86400
    sessions = [s for s in list_sessions(config) if s[1] >= cutoff][:max_sessions]
    parts = []
    for sid, _mtime, _n in sessions:
        s = Session(config, session_id=sid)
        t = s.transcript()
        if t:
            parts.append(f"=== 会话 {sid} ===\n{t}")
    return "\n\n".join(parts)


def generate_drafts(config: Config) -> dict | None:
    """用强模型复盘近期对话，返回整理草稿 dict（无内容返回 None）。"""
    transcripts = _recent_transcripts(config)
    if not transcripts:
        return None

    existing = longterm.list_all(config)
    existing_text = "\n".join(f"- {m.get('memory', m)}" for m in existing) or "（空）"
    blocks = MemoryBlocks(config)

    agent = Agent(
        build_model(config, config.models.memory_write),
        instructions=CONSOLIDATE_INSTRUCTIONS,
    )
    prompt = (
        f"## 当前 human 块内容\n{blocks.read('human')}\n\n"
        f"## 已有长期记忆\n{existing_text}\n\n"
        f"## 近期对话记录\n{transcripts}\n\n"
        "请输出整理草稿 JSON。"
    )
    result = agent.run_sync(prompt)
    text = result.output.strip()
    # 容错：剥离可能的 markdown 代码围栏
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": "模型输出不是合法 JSON", "raw": text[:1000]}


def apply_drafts(config: Config, drafts: dict,
                 confirm=lambda msg: False) -> list[str]:
    """逐项确认并应用草稿。返回已应用项的描述列表。"""
    applied: list[str] = []

    for entry in drafts.get("memory_entries") or []:
        if confirm(f"写入长期记忆：\n  {entry}\n确认？"):
            mem = longterm.get_memory(config)
            if mem is not None:
                try:
                    mem.add([{"role": "user", "content": entry}], user_id=longterm.USER_ID)
                    applied.append(f"记忆条目：{entry[:50]}")
                except Exception:
                    pass

    human_update = drafts.get("human_block_update")
    if human_update and confirm(
        f"更新 human 块（用户画像）：\n{human_update[:300]}\n确认？"
    ):
        blocks = MemoryBlocks(config)
        blocks.update("human", human_update, reason="consolidate 复盘更新")
        applied.append("human 块已更新（git 已提交）")

    for sug in drafts.get("rule_suggestions") or []:
        if confirm(
            f"建议新增规则「{sug.get('name')}」（{sug.get('reason')}）：\n"
            f"{sug.get('content', '')[:200]}\n确认？"
        ):
            rules_dir = config.path(config.paths.rules_dirs[0])
            rules_dir.mkdir(parents=True, exist_ok=True)
            path = rules_dir / f"{sug['name']}.md"
            path.write_text(
                f"---\ndescription: {sug.get('reason', sug['name'])}\n"
                f"alwaysApply: true\n---\n\n{sug.get('content', '')}\n",
                encoding="utf-8",
            )
            applied.append(f"规则文件：{path.name}")

    for stale in drafts.get("stale_memories") or []:
        if confirm(f"删除过时记忆：\n  {stale}\n确认？"):
            # mem0 里按内容找 id 删除
            for m in longterm.list_all(config):
                if m.get("memory") == stale or stale in str(m.get("memory", "")):
                    if longterm.delete(config, m.get("id")):
                        applied.append(f"删除过时记忆：{stale[:50]}")
                    break

    return applied
