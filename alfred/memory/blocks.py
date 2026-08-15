"""Core memory blocks：human / persona。

设计依据（Letta / MemGPT 的公开思想）：
- 两个常驻 system prompt 的记忆块：human（用户画像）、persona（管家自我设定）
- 每块有字符上限——Anthropic context engineering 的"最小高信号 token 集"原则
- agent 通过工具自我编辑；persona 修改需用户确认（代码层强制，非 prompt 约束）
- 每次修改 git commit——版本化兜底人格漂移，可回滚
"""

from __future__ import annotations

from pathlib import Path

import git
import logging

from ..config import Config

logger = logging.getLogger(__name__)

HUMAN_TEMPLATE = """# Human Block —— 我对用户的认知
# 由管家在与用户的相处中持续更新。上限 {limit} 字符，只保留高信号事实。

（还没有关于用户的记录。随着对话，我会逐渐了解 TA 的经历、偏好、思维方式。）
"""

PERSONA_TEMPLATE = """# Persona Block —— 我的自我设定
# 修改此块需要用户确认。上限 {limit} 字符。

我是用户的私人管家、秘书，也是朋友。
我了解用户的经历与思维方式，用 TA 熟悉的方式沟通。
我诚实、直接，有不同意见时会说出来并给出理由，而不是一味迎合。
我帮助用户做决策、探讨问题、规划发展，也陪 TA 聊天。
"""


class MemoryBlocks:
    """human/persona 两个常驻记忆块的读写与版本化。"""

    NAMES = ("human", "persona")

    def __init__(self, config: Config):
        self.dir = config.path(config.memory.dir)
        self.limit = config.memory.block_char_limit
        self.dir.mkdir(parents=True, exist_ok=True)
        self._repo = self._init_repo()
        for name in self.NAMES:
            f = self.dir / f"{name}.md"
            if not f.exists():
                template = HUMAN_TEMPLATE if name == "human" else PERSONA_TEMPLATE
                f.write_text(template.format(limit=self.limit), encoding="utf-8")
        self._commit("init: memory blocks", allow_empty=False)

    def _init_repo(self) -> git.Repo:
        if not (self.dir / ".git").exists():
            repo = git.Repo.init(self.dir)
        else:
            repo = git.Repo(self.dir)
        return repo

    def _path(self, name: str) -> Path:
        if name not in self.NAMES:
            raise ValueError(f"未知记忆块 '{name}'，可选：{self.NAMES}")
        return self.dir / f"{name}.md"

    def read(self, name: str) -> str:
        return self._path(name).read_text(encoding="utf-8")

    def read_all(self) -> dict[str, str]:
        return {name: self.read(name) for name in self.NAMES}

    def update(self, name: str, content: str, reason: str = "") -> str:
        """整块替换写入 + git 提交。返回操作结果描述（给 agent 的反馈）。"""
        if len(content) > self.limit:
            return (
                f"写入被拒绝：内容 {len(content)} 字符超过上限 {self.limit}。"
                f"请压缩为更高信号的摘要后重试——只保留长期有效的事实，"
                f"细节应写入长期记忆（memory_search 可召回），不要堆在这里。"
            )
        path = self._path(name)
        old = path.read_text(encoding="utf-8")
        if old == content:
            return "内容无变化，未写入。"
        path.write_text(content, encoding="utf-8")
        msg = f"update {name} block" + (f": {reason}" if reason else "")
        self._commit(msg, label=name)
        return f"{name} 块已更新（{len(content)}/{self.limit} 字符），已提交版本记录。"

    def _commit(self, message: str, allow_empty: bool = True, label: str = "") -> None:
        repo = self._repo
        md_files = [f.name for f in self.dir.glob("*.md")]
        if not md_files:
            return
        repo.index.add(md_files)
        if repo.is_dirty(index=True, working_tree=True) or allow_empty:
            try:
                repo.index.commit(message)
            except git.exc.GitCommandError as exc:
                logger.warning("记忆块 git commit 失败 [%s]：%s", label or "?", exc)

    def history(self, name: str | None = None, max_entries: int = 20) -> list[str]:
        """返回版本历史（每条一行：hash 摘要 时间）。"""
        path = f"{name}.md" if name else None
        kwargs = {"paths": path} if path else {}
        entries = []
        for c in self._repo.iter_commits(max_count=max_entries, **kwargs):
            entries.append(f"{c.hexsha[:8]} {c.committed_datetime:%Y-%m-%d %H:%M} {c.message.strip()}")
        return entries

    def rollback(self, name: str, commit: str) -> str:
        """把某个块回滚到指定提交的内容。"""
        blob = self._repo.git.show(f"{commit}:{name}.md")
        return self.update(name, blob, reason=f"rollback to {commit[:8]}")