"""RefleXion 教训记忆块。

设计依据（Shinn et al. 2023, "RefleXion: Language Agents with Verbal
Reasoning"）：agent 不通过更新权重学习，而是通过**语言化的反思**——把
任务反馈（工具失败、用户纠正、行为被拒绝）转化为文本教训，存储到
情景记忆缓冲中，下次类似任务自动注入 prompt。

与 human/persona 块的区别：
- human/persona：整块替换，是"你是谁"和"我对你是谁"的当前画像
- lessons：追加型，是"我过去在类似情况中学到了什么"的经验库
"""

from __future__ import annotations

from datetime import datetime

import git
import logging

from ..config import Config

logger = logging.getLogger(__name__)

LESSONS_TEMPLATE = """# Lessons Block —— 管家的成长教训
# 来源于过去对话的反思，RefleXion 风格。每条教训对应一类场景，
# 当遇到类似场景时自动激活，指导决策。

_（还没有教训记录。随着对话中遇到的问题、纠正和复盘，教训会逐渐积累。）_
"""

# 超过上限后触发压缩，保留最近 N 条。
# 上限本身从 config.memory.lessons_block_char_limit 读取（默认 4000），不再硬编码。
LESSONS_KEEP_ON_COMPRESS = 20


class LessonsBlock:
    """追加型教训记忆块，RefleXion 风格。"""

    NAME = "lessons"

    def __init__(self, config: Config):
        self.dir = config.path(config.memory.dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{self.NAME}.md"
        self.max_chars = config.memory.lessons_block_char_limit
        self._repo = self._ensure_repo()
        if not self.path.exists():
            self.path.write_text(LESSONS_TEMPLATE, encoding="utf-8")
            self._commit("init: lessons block", allow_empty=False)

    def _ensure_repo(self) -> git.Repo:
        # lessons 和 human/persona 共用同一个 memory .git 仓库
        git_dir = self.dir / ".git"
        if git_dir.exists():
            return git.Repo(self.dir)
        return git.Repo.init(self.dir)

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def add(self, category: str, lesson: str, context: str = "") -> str:
        """追加一条教训。返回操作结果描述。

        Args:
            category: 教训类别标签（如 "code-debug" / "tone" / "workflow"）
            lesson: 教训正文，一句话要点
            context: 触发这条教训的具体场景（供 agent 参考）
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = (
            f"\n\n### [{category}] {now}\n"
            f"**教训**：{lesson}\n"
        )
        if context:
            entry += f"**场景**：{context}\n"

        current = self.read()
        new_content = current.rstrip() + entry

        if len(new_content) > self.max_chars:
            new_content = self._compress(current) + entry
            if len(new_content) > self.max_chars:
                return f"教训块容量已满（{self.max_chars} 字符），请先运行 consolidate 压缩后再追加。"

        self.path.write_text(new_content, encoding="utf-8")
        self._commit(f"append lesson [{category}]: {lesson[:30]}")
        return f"教训已追加：[{category}] {lesson[:40]}"

    def list_lessons(self) -> list[dict]:
        """解析当前教训块，返回教训列表。"""
        text = self.read()
        lessons = []
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("### ["):
                bracket = line.find("]")
                if bracket != -1:
                    category = line[5:bracket]
                    rest = line[bracket + 1:].strip()
                    lessons.append({"category": category, "title": rest})
        return lessons

    def _compress(self, current: str) -> str:
        """超过上限时压缩：保留最近的 LESSONS_KEEP_ON_COMPRESS 条教训。"""
        lines = current.split("\n")
        entries: list[list[str]] = []
        current_entry: list[str] = []
        for line in lines:
            if line.startswith("### ["):
                if current_entry:
                    entries.append(current_entry)
                current_entry = [line]
            else:
                current_entry.append(line)
        if current_entry:
            entries.append(current_entry)

        # 保留 header（模板前几行）+ 最近的 N 条
        header = "\n".join(lines[:3])
        if not header.strip():
            header = LESSONS_TEMPLATE.strip()

        kept = entries[-LESSONS_KEEP_ON_COMPRESS:]
        compressed = header + "\n" + "\n\n".join("\n".join(e) for e in kept) + "\n"
        logger.info("lessons 块压缩：%d 条 → %d 条", len(entries), len(kept))
        return compressed

    def _commit(self, message: str, allow_empty: bool = True) -> None:
        repo = self._repo
        if not repo:
            return
        repo.index.add([f"{self.NAME}.md"])
        if repo.is_dirty(index=True, working_tree=True) or allow_empty:
            try:
                repo.index.commit(message)
            except git.exc.GitCommandError as exc:
                logger.warning("教训块 git commit 失败：%s", exc)