"""Skills 加载器：三级披露。

三级披露：
  ① 启动时只注入 name+description（索引层）——刚好够 LLM 判断"何时该用"
  ② agent 判定相关后自动将全文注入 prompt（LLM 从真实流程文本推理）
  ③ SKILL.md 按名引用同目录资源文件，agent 按需再读

**关键设计**：不做前端关键词匹配。匹配判断交给 LLM 从 description 推理完成。
原因：
  - 前端关键词匹配是模板化逻辑，无法理解语义（n-gram "开发" 会误命中 brandkit）
  - description 本身已经写了触发场景（"当用户要 X 时使用"），LLM 能读懂
  - 保持对第三方 skill 的零侵入——只读 name + description，不要求额外字段
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import Config

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass
class SkillMeta:
    name: str
    description: str
    path: Path  # SKILL.md 的路径


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
        return None
    return SkillMeta(name=name, description=description, path=path)


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


def render_skills_index(skills: list[SkillMeta]) -> str:
    """注入 system prompt 的索引文本（三级披露的入口）。

    这是唯一的注入方式。索引包含每个 skill 的 name + description + file path，
    由 LLM 自行判断任务是否与某个 skill 相关，相关则用 file_read 读取全文。
    """
    if not skills:
        return ""
    lines = [
        "## 可用技能（skills）",
        "以下是你具备的技能。当用户任务与某个技能的描述匹配时，请用 file_read 工具读取其 SKILL.md 全文，然后按其中的流程执行。不需要用户主动要求，你自己判断并用。",
        "",
    ]
    for s in skills:
        lines.append(f"- **{s.name}**：{s.description}（文件：{s.path}）")
    return "\n".join(lines)


def find_skill(config: Config, name: str) -> SkillMeta | None:
    for s in scan_skills(config):
        if s.name == name:
            return s
    return None