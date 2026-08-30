"""Sleep-time 整理：复盘 → 三类草稿 → 用户确认 → 入库（git 版本化）。

设计依据：
- Letta sleep-time compute：后台用强模型把原始对话转化为学习后的记忆
- Devin Knowledge Suggestions：从反馈中沉淀知识，但生成的是"建议草稿"，
  由用户确认——不做全自动无确认的自我改写
- 产物四类：长期记忆条目（mem0）/ 规则修订（rules/）/ human block 更新建议 /
  情景记忆四元组（episodes）
- mem0 v3 开源版 ADD-only 的整合缺口在此补足：复盘时对矛盾记忆给出处理建议
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic_ai import Agent

from ..config import Config
from ..history import list_sessions
from ..llm import build_model
from . import longterm
from .blocks import MemoryBlocks
from .lessons import LessonsBlock

CONSOLIDATE_INSTRUCTIONS = """你是私人管家的"睡眠整理"模块。你的任务是复盘管家与用户的近期对话，
提炼出值得长期保留的内容，产出结构化的整理草稿。

输出严格的 JSON（不要输出任何其他内容）：
{
  "memory_entries": ["值得写入长期记忆的事实，每条一句，用第三人称描述用户", ...],
  "human_block_update": "若对用户的整体认知有变化，给出 human 块的完整新内容（保持原有章节结构）；无变化则为 null",
  "rule_suggestions": [{"name": "规则名", "content": "规则正文", "reason": "为什么建议"}],  // 用户反复纠正管家的行为模式才提
  "stale_memories": ["与最新信息矛盾的已有记忆原文"],  // 供用户确认后删除
  "lessons": [{"category": "场景类别", "lesson": "一句教训", "context": "触发场景"}],  // RefleXion：从问题中提炼的经验
  "episodes": [{"situation": "场景", "thoughts": "思路", "action": "行动", "result": "结果"}]  // 成功的处理案例（四元组），只有确认成功完成任务时才产出
}

提炼标准：
- 只保留长期有效的事实（偏好、经历、决策、目标、关系、习惯），日常琐事不要
- 用户明确纠正过管家的地方优先沉淀
- human_block_update 要压缩到高信号画像，不是流水账

RefleXion 教训（lessons）提炼标准（Shinn et al. 2023）：
- 仅当管家在某次对话中出现了**可改进的错误、低效、遗漏**时才提炼
- 教训要简洁可执行，格式类似"当遇到 X 场景时，优先做 Y"
- 避免空泛的"下次做得更好"，要具体到场景和策略
- category 可选值建议：code-debug / workflow / tone / tool-usage / knowledge-gap / self-awareness
- 没有可提炼的教训时，lessons 字段为空数组
"""

import re as _re

# 用户事实模式：从 memory_entries 中识别出真正关于用户的事实条目
_USER_FACT_PATTERNS = (
    r"用户", r"他叫", r"他是", r"她是",
    r"喜欢", r"偏好", r"习惯",
    r"是.*岁", r"来自", r"毕业", r"工作",
    r"公众号", r"写.*博客", r"写.*公众号",
    r"性格", r"MBTI", r"INFP", r"INTJ", r"ENFP",
    r"关系", r"伴侣", r"家人",
)
# agent 自我认知 / 系统类，应排除
_AGENT_FACT_PATTERNS = (
    r"工具管线", r"code_patch", r"pytest", r"测试.*通过",
    r"重构", r"改造", r"升级", r"实现",
    r"ToolDeniedError", r"ToolExecutionPipeline",
    r"memory_search", r"memory_update_block",
)
# 自动沉淀到 human 块的章节头（复用已有标题，避免重复追加）
_AUTO_FACTS_HEADER = "## 自动沉淀的用户事实（consolidate 自动写入）"


def _extract_user_facts_from_memories(entries: list[str]) -> list[str]:
    """从 memory_entries 中筛选出纯用户事实，排除 agent 自我认知。"""
    user_facts: list[str] = []
    for entry in entries:
        if any(_re.search(p, entry, _re.IGNORECASE) for p in _USER_FACT_PATTERNS):
            if not any(_re.search(p, entry, _re.IGNORECASE) for p in _AGENT_FACT_PATTERNS):
                user_facts.append(entry)
    return user_facts


def _apply_user_facts_to_human(config: Config, facts: list[str]) -> list[str]:
    """把 user facts 增量追加到 human block。

    幂等：已存在的事实不重复写，章节头只出现一次。
    空间不足时按顺序尽量多写，写不下的返回 [跳过: ...] 而不是整批放弃。
    """
    blocks = MemoryBlocks(config)
    current = blocks.read("human")
    new_facts = [f for f in facts if f not in current]
    if not new_facts:
        return []

    limit = blocks.limit_for("human")
    # 章节头已存在则复用，避免每次复盘都追加一个重复的 ## 标题
    header = "" if _AUTO_FACTS_HEADER in current else _AUTO_FACTS_HEADER

    body = ""
    written: list[str] = []
    head = f"{header}\n" if header else ""
    for fact in new_facts:
        line = f"- {fact}\n"
        # 直接用最终字符串的长度做判断，避免估算与实际写入不一致
        if len(current.rstrip() + "\n\n" + head + body + line) > limit:
            break
        body += line
        written.append(fact)

    if not written:
        return [f"[跳过: 超出 human 块上限 {limit} 字符]" for _ in new_facts]

    updated = current.rstrip() + "\n\n" + head + body
    blocks.update("human", updated, reason="auto-consolidate: user facts from memory")
    applied = [f"用户事实→human块：{r[:50]}" for r in written]
    skipped = [f"[跳过: 超出 human 块上限 {limit} 字符]" for f in new_facts[len(written):]]
    return applied + skipped


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

    # RefleXion 教训：agent 自我改进，无需用户确认（类比情景记忆自动写入）
    for item in drafts.get("lessons") or []:
        category = item.get("category", "general")
        lesson = item.get("lesson", "")
        context = item.get("context", "")
        if lesson:
            try:
                lessons_block = LessonsBlock(config)
                result = lessons_block.add(category, lesson, context)
                applied.append(f"RefleXion 教训：{result}")
            except Exception:
                pass

    # 情景记忆：与 apply_unattended 一致，否则交互模式下 LLM 产出的 episodes 会被静默丢弃
    for item in drafts.get("episodes") or []:
        situation = item.get("situation", "")
        result = item.get("result", "")
        if not (situation and result):
            continue
        if confirm(
            f"保存情景记忆（成功案例四元组）：\n  场景：{situation[:80]}\n  结果：{result[:80]}\n确认？"
        ):
            try:
                from .episodic import Episode, save_episode
                ep = Episode(
                    situation=situation,
                    thoughts=item.get("thoughts", ""),
                    action=item.get("action", ""),
                    result=result,
                )
                save_episode(config, ep)
                applied.append(f"情景记忆：{situation[:50]}")
            except Exception:
                pass

    return applied


def apply_unattended(config: Config, drafts: dict) -> list[str]:
    """无人值守模式：自动写入 lessons / memory_entries / episodes，
    human_block_update 视改动大小自动或待审，其余草稿暂存待审查。

    策略：
    - lessons：无脑写（RefleXion 教训，agent 自改进，不需要用户确认）
    - memory_entries：无脑写（用户事实沉淀，ADD-only，风险低）
    - episodes：无脑写（情景记忆四元组，结构化案例，风险低）
    - human_block_update：改动 ≤ AUTO_HUMAN_UPDATE_MAX_CHARS 直接写，
      否则降级为 pending 待用户下次 /consolidate-review 确认
    - rule_suggestions / stale_memories：始终待审

    返回已应用项的描述列表。暂存草稿写入 data/history/consolidate_pending.jsonl，
    供 /consolidate-review 命令调出。
    """
    from pathlib import Path as _Path
    import json as _json
    from .consolidate_state import AUTO_HUMAN_UPDATE_MAX_CHARS

    applied: list[str] = []

    # 1) lessons 自动写入
    for item in drafts.get("lessons") or []:
        category = item.get("category", "general")
        lesson = item.get("lesson", "")
        context = item.get("context", "")
        if lesson:
            try:
                lessons_block = LessonsBlock(config)
                result = lessons_block.add(category, lesson, context)
                applied.append(f"RefleXion 教训：{result}")
            except Exception:
                pass

    # 2) memory_entries 自动写入（用户事实沉淀，非敏感）
    for entry in drafts.get("memory_entries") or []:
        try:
            mem = longterm.get_memory(config)
            if mem is not None:
                mem.add([{"role": "user", "content": entry}], user_id=longterm.USER_ID)
                applied.append(f"记忆条目：{entry[:50]}")
        except Exception:
            pass

    # 3) episodes 自动写入（情景记忆四元组）
    from .episodic import Episode, save_episode as _do_save_episode

    for item in drafts.get("episodes") or []:
        try:
            ep = Episode(
                situation=item.get("situation", ""),
                thoughts=item.get("thoughts", ""),
                action=item.get("action", ""),
                result=item.get("result", ""),
            )
            if ep.situation and ep.result:
                _do_save_episode(config, ep)
                applied.append(f"情景记忆：{ep.situation[:30]}")
        except Exception:
            pass

    # 3.5) memory_entries → human block 自动晋升（Updating operation）
    #
    # 仅当 LLM 没有产出 human_block_update 草稿时启用。
    # 如果 LLM 已经生成了 human_block_update，说明它已经识别并整合了这些事实，
    # 无需重复写入——走 4) 的整块替换路径即可。
    #
    # 对齐 Rethinking Memory (2505.00675) 的 Updating operation：
    # mem0 层（implicit contextual）的发现触发 human block 层（explicit contextual）的更新。
    human_update_draft = drafts.get("human_block_update")
    _facts_promoted = []
    if not human_update_draft:
        _user_facts_from_memories = _extract_user_facts_from_memories(
            drafts.get("memory_entries") or [],
        )
        if _user_facts_from_memories:
            _facts_promoted = _apply_user_facts_to_human(config, _user_facts_from_memories)
            applied.extend(_facts_promoted)

    # 4) human_block_update：按改动幅度决定
    human_update = human_update_draft
    pending: dict[str, Any] = {}
    if human_update:
        try:
            blocks = MemoryBlocks(config)
            current = blocks.read("human")
            current_real = len(
                current.replace("_（", "").replace("）_", "")
                .replace("# ", "").replace("\n", "")
            )
            delta = len(human_update) - current_real
            if abs(delta) <= AUTO_HUMAN_UPDATE_MAX_CHARS:
                result = blocks.update("human", human_update, reason="auto-consolidate")
                applied.append(str(result))
            else:
                pending["human_block_update"] = human_update
        except Exception:
            pending["human_block_update"] = human_update

    # 5) 其余草稿暂存
    if drafts.get("rule_suggestions"):
        pending["rule_suggestions"] = drafts["rule_suggestions"]
    if drafts.get("stale_memories"):
        pending["stale_memories"] = drafts["stale_memories"]

    if pending:
        pending_path = (
            _Path(config.path(config.paths.history_dir))
            / "consolidate_pending.jsonl"
        )
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        with open(pending_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps({
                "ts": datetime.now().timestamp(),
                "drafts": pending,
            }) + "\n")

    return applied
