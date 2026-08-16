"""Skills 加载器：三级披露。

三级披露：
  ① 启动时只注入 name+description+when-to-use（索引层）——刚好够 LLM 判断"何时该用"
  ② agent 判定相关后自动将全文注入 prompt（LLM 从真实流程文本推理）
  ③ SKILL.md 按名引用同目录资源文件，agent 按需再读
**关键设计**：不做前端关键词匹配。匹配判断交给 LLM 从 description 推理完成。
原因：
  - 前端关键词匹配是模板化逻辑，无法理解语义（n-gram "开发" 会误命中 brandkit）
  - description 本身已经写了触发场景（"当用户要 X 时使用"），LLM 能读懂
  - 保持对第三方 skill 的零侵入——只读 name + description，不要求额外字段
**可选增强字段**（仿 Kimi Code skill 机制，零侵入，老 skill 不受影响）：
  - when-to-use：触发场景的精确描述，LLM 判定是否调用时参考（description 的补充）
  - disable-model-invocation: true：标记为"只能用户主动触发"，LLM 不会自动调用
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import Config

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


@dataclass
class SkillMeta:
    name: str
    description: str
    path: Path  # SKILL.md 的路径
    when_to_use: str = ""
    disable_model_invocation: bool = False


def _get_bool(meta: dict, key: str) -> bool:
    v = meta.get(key)
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    return False


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
    when_to_use = str(meta.get("when-to-use", "") or meta.get("when_to_use", "") or "").strip()
    disable_model_invocation = _get_bool(meta, "disable-model-invocation") or _get_bool(meta, "disable_model_invocation")
    return SkillMeta(
        name=name,
        description=description,
        path=path,
        when_to_use=when_to_use,
        disable_model_invocation=disable_model_invocation,
    )


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

    索引包含每个 skill 的 name + description + when-to-use + file path，
    由 LLM 自行判断任务是否与某个 skill 相关，相关则用 file_read 读取全文。

    带有 disable_model_invocation 的 skill 会单独标记，并附一条显式禁令，
    防止 LLM 擅自调用那些只应"用户主动触发"的技能。

    幂等提醒：已用 file_read 读过某个 SKILL.md 且当前上下文仍在，
    不需要再读一遍；直接按其中流程继续执行。
    """
    if not skills:
        return ""

    invocable = [s for s in skills if not s.disable_model_invocation]
    disabled = [s for s in skills if s.disable_model_invocation]

    lines: list[str] = [
        "## 可用技能（skills）",
        "以下是你具备的技能。当用户任务与某个技能的描述或 when-to-use 匹配时，请用 file_read 工具读取其 SKILL.md 全文，然后按其中的流程执行。不需要用户主动要求，你自己判断并用。",
        "如果某次对话中你已经通过 file_read 读过某个 SKILL.md 且当前上下文仍存在，不要重复读取同一文件——直接按其中流程继续执行。",
        "",
    ]
    for s in invocable:
        desc = s.description
        wtu = s.when_to_use
        if wtu and wtu.lower() != desc.lower():
            lines.append(f"- **{s.name}**：{desc}  触发：{wtu}（文件：{s.path}）")
        else:
            lines.append(f"- **{s.name}**：{desc}（文件：{s.path}）")
    if disabled:
        lines.append("")
        lines.append("以下技能仅能通过用户主动指令触发，你不得自行调用：")
        for s in disabled:
            lines.append(f"- {s.name}（文件：{s.path}）")

    return "\n".join(lines)


def find_skill(config: Config, name: str) -> SkillMeta | None:
    for s in scan_skills(config):
        if s.name == name:
            return s
    return None