"""规则文件加载器：Cursor rules 式 frontmatter 四触发器。

规则 = rules 目录下的 .md 文件，frontmatter 控制激活方式：
  ---
  description: 代码审查规范        # 智能召回的判据
  globs: *.py,src/**               # 可选：按文件 glob 匹配
  alwaysApply: false               # true = 每轮常驻注入
  ---
  （正文）

四种触发：alwaysApply 常驻 / description 智能召回 / globs 匹配 / 手动提及。
本期实现：alwaysApply 常驻 + description 索引（由 agent 用 file_read 激活），
globs 与手动为语义预留。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import Config

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass
class Rule:
    name: str
    path: Path
    description: str = ""
    globs: list[str] = field(default_factory=list)
    always_apply: bool = False
    body: str = ""


def parse_rule(path: Path) -> Rule | None:
    text = path.read_text(encoding="utf-8")
    meta, body = {}, text
    m = FRONTMATTER_RE.match(text)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            return None
        body = text[m.end():]
    globs = meta.get("globs") or []
    if isinstance(globs, str):
        globs = [g.strip() for g in globs.split(",") if g.strip()]
    return Rule(
        name=path.stem,
        path=path,
        description=str(meta.get("description", "")).strip(),
        globs=globs,
        always_apply=bool(meta.get("alwaysApply", False)),
        body=body.strip(),
    )


def scan_rules(config: Config) -> list[Rule]:
    rules: list[Rule] = []
    seen: set[str] = set()
    for d in config.paths.rules_dirs:
        root = config.path(d)
        if not root.exists():
            continue
        for f in sorted(root.glob("**/*.md")):
            if f.stem in seen:
                continue
            rule = parse_rule(f)
            if rule:
                rules.append(rule)
                seen.add(f.stem)
    return rules


def render_rules(rules: list[Rule]) -> tuple[str, str]:
    """返回 (常驻规则文本, 可召回规则索引)。"""
    always = [r for r in rules if r.always_apply]
    recallable = [r for r in rules if not r.always_apply and r.description]

    always_text = ""
    if always:
        parts = ["## 常驻规则（必须始终遵守）"]
        parts += [r.body for r in always]
        always_text = "\n\n".join(parts)

    index_text = ""
    if recallable:
        lines = [
            "## 可召回的规则",
            "以下规则在相关时生效。需要时用 file_read 读取全文并遵守。",
            "",
        ]
        for r in recallable:
            lines.append(f"- **{r.name}**：{r.description}（文件：{r.path}）")
        index_text = "\n".join(lines)

    return always_text, index_text
