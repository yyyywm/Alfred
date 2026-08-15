"""Skills 加载器：三级披露 + 主动匹配。

三级披露：
  ① 启动时只注入 name+description（~100 tokens/skill）——刚好够判断"何时该用"
  ② agent 判定相关后用 file_read 工具读 SKILL.md 正文
  ③ SKILL.md 按名引用同目录资源文件，agent 按需再读

主动匹配（基于 SkillRet / SkillFlow / Skill-Use 论文）：
  - SkillMeta 含 triggers（关键词列表），与用户输入做 OR 匹配
  - 匹配的 skill 全文注入 prompt（LLM 从真实流程文本推理，不是模板匹配）
  - 后置兜底：TurnEnd 检查匹配的 skill 是否被 file_read 读取过，
    没读过则发 SkillSuggested 事件提醒用户
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import Config

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)

# 单次注入的 skill 上限（防止 prompt 膨胀）
_MAX_INJECTED = 3


@dataclass
class SkillMeta:
    name: str
    description: str
    path: Path  # SKILL.md 的路径
    triggers: list[str]  # 触发关键词列表，匹配用户输入时全文注入

    @property
    def dir(self) -> Path:
        return self.path.parent


def parse_skill_md(path: Path) -> SkillMeta | None:
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        return None
    name = meta.get("name") or path.parent.name
    description = str(meta.get("description", "")).strip()
    if not description:
        return None  # description 是触发判据，缺失的 skill 不注入
    triggers_raw = meta.get("triggers", [])
    triggers: list[str] = []
    if isinstance(triggers_raw, list):
        triggers = [str(t).strip().lower() for t in triggers_raw if str(t).strip()]
    elif isinstance(triggers_raw, str):
        triggers = [triggers_raw.strip().lower()]
    # description 也作为触发源（避免写 triggers 的用户被排除）
    desc_lower = description.lower()
    triggers.extend(desc_lower.split())
    triggers.extend(desc_lower.split("，"))
    return SkillMeta(name=name, description=description, path=path, triggers=triggers)


def scan_skills(config: Config) -> list[SkillMeta]:
    """扫描配置的技能目录。后面的目录优先级低（同名前者覆盖）。"""
    seen: dict[str, SkillMeta] = {}
    for d in config.paths.skills_dirs:
        root = config.path(d)
        if not root.exists():
            continue
        for f in sorted(root.glob("*/SKILL.md")):
            meta = parse_skill_md(f)
            if meta and meta.name not in seen:
                seen[meta.name] = meta
    return list(seen.values())


def match_skills(skills: list[SkillMeta], user_input: str) -> list[SkillMeta]:
    """基于关键词匹配用户输入。返回匹配的 skill 列表（最多 _MAX_INJECTED 个）。

    匹配规则：user_input 中包含任一 trigger 关键词即匹配（OR 逻辑，宁多勿少）。
    不做评分/排序——保持简单，后续 SkillSuggested 兜底机制保证漏匹配也能补救。
    """
    text = user_input.lower()
    matched: list[SkillMeta] = []
    for s in skills:
        for kw in s.triggers:
            if len(kw) < 2:  # 跳过单字（噪声太高）
                continue
            if kw in text:
                matched.append(s)
                break
    return matched[:_MAX_INJECTED]


def render_skills_index(skills: list[SkillMeta]) -> str:
    """注入 system prompt 的索引文本（三级披露的入口）。"""
    if not skills:
        return ""
    lines = [
        "## 可用技能（skills）",
        "以下是你的技能索引。当任务与某个技能的描述匹配时，先用 file_read 工具读取其 SKILL.md 全文，再按其中的流程执行。",
        "",
    ]
    for s in skills:
        triggers_str = f" 触发词：{', '.join(s.triggers[:5])}" if s.triggers else ""
        lines.append(f"- **{s.name}**：{s.description}{triggers_str}（文件：{s.path}）")
    return "\n".join(lines)


def render_injected_skills(skills: list[SkillMeta]) -> str:
    """把匹配的 skill 全文注入 prompt（LLM 从真实流程文本推理）。"""
    if not skills:
        return ""
    parts = ["## 当前任务匹配的技能（请阅读并遵循以下流程）", ""]
    for s in skills:
        try:
            body = s.path.read_text(encoding="utf-8")
            # 去掉 frontmatter，只保留正文
            m = FRONTMATTER_RE.match(body)
            if m:
                body = body[m.end():].strip()
        except OSError:
            body = f"（无法读取 {s.path}）"
        parts.append(f"### {s.name} — {s.description}")
        parts.append(body)
        parts.append("")
    return "\n".join(parts)


def find_skill(config: Config, name: str) -> SkillMeta | None:
    for s in scan_skills(config):
        if s.name == name:
            return s
    return None