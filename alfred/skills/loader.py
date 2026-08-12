"""Skills 加载器：Anthropic Agent Skills 的 progressive disclosure 三级。

三级披露：
  ① 启动时只注入 name+description（~100 tokens/skill）——刚好够判断"何时该用"
  ② agent 判定相关后用 file_read 工具读 SKILL.md 正文
  ③ SKILL.md 按名引用同目录资源文件，agent 按需再读

SKILL.md 格式（frontmatter + 正文）：
  ---
  name: software-dev-workflow
  description: 软件开发标准流程：需求→结构化→产品→研发→测试→上线。当用户要开发/上线软件时使用。
  ---
  （正文，推荐 Devin Playbook 分节：Procedure / Specifications / Advice /
   Forbidden Actions / Required from User）
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
    """注入 system prompt 的索引文本（第三级披露的入口）。"""
    if not skills:
        return ""
    lines = [
        "## 可用技能（skills）",
        "以下是你的技能索引。当任务与某个技能的描述匹配时，先用 file_read 工具读取其 SKILL.md 全文，再按其中的流程执行。",
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
