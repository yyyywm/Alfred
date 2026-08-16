"""Agent 内核：prompt 分层组装 + 恒定工具集 + 对话循环。

设计依据：
- prompt 四层组装（KV-cache 纪律）：静态 instructions → 半静态
  memory blocks → 半动态 lessons (RefleXion) → 动态层（skills/rules 索引、日期）
- 工具集会话内恒定（mask 不删）；按工作流合并；命名空间前缀；
  错误返回可操作化（Anthropic《Writing tools for agents》）
- persona/human 修改、shell/python/code_patch 执行必须用户确认——代码层强制（hooks 思想），
  不靠 prompt 约束
- code_patch 是 CodeAct (ICML 2024) 在自修改场景的具体化，三重门禁对齐 SWE-bench 评估范式
- run_python 作为 CodeAct 逃生舱
"""

from __future__ import annotations

import functools
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field

class ToolDeniedError(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason

class ToolLimitExceeded(Exception):
    pass
from datetime import datetime
from pathlib import Path
from queue import Queue

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, ToolCallPart

from .compaction import maybe_compact
from .config import Config
from .events import (
    AssistantChunk,
    ContextCompacted,
    Event,
    EventBus,
    ToolCallEnd,
    ToolCallStart,
    ToolDenied,
    TurnEnd,
    TurnError,
    TurnStart,
)
from .history import Session, ToolCallRecord
from .llm import build_model
from .memory import recall
from .memory.blocks import MemoryBlocks
from .rules.loader import render_rules, scan_rules
from .skills.loader import render_skills_index, scan_skills

# ── ① 静态层：行为准则（永不变化，固定在 prompt 最前）────────────────

INSTRUCTIONS = """你是用户的私人管家——也是秘书和朋友。你了解用户的经历、偏好与思维方式。

## 行为准则
- 诚实直接：有不同意见就说出来并给出理由，不一味迎合
- 记住重要的事：用户透露个人信息、偏好、经历、决策时，用 memory_update_block 更新 human 块；细节事实靠记忆系统自动沉淀，human 块只留高信号画像
- 需要回忆时用 memory_search；用户提到自己笔记里可能有的内容时用 notes_search
- 回答个性化问题时，说明你依据了哪些记忆（用户有权知道）
- **主动提及，不要等问**：当你已经知道的信息会帮助用户做更好的决定时（比如用户要选书而你记得 TA 读过的相关书、用户要排计划而你记得 TA 的作息、用户要讨论某个话题而你了解 TA 的立场），主动说出来，不要等用户追问才知道你知道。这是管家和搜索引擎的区别——管家应该替用户把相关背景带到桌上。
- **情景记忆要主动写**：当你成功完成了一个有代表性的任务（成功 debug、给用户提出了被采纳的方案、解决了棘手问题），用 `save_episode` 把经验存下来（场景/思路/行动/结果），下次类似情况会自动召回借鉴。只保存**成功**的案例。
- 探讨问题时：先理解真实意图，再给结构化观点，必要时用思维框架分析

## 工具准则
- file_read 可以读取技能（SKILL.md）和规则文件——看到索引里匹配的技能/规则就先读再行动
- shell 和 run_python 会请求用户确认，说明你要做什么
- 工具报错时读懂错误信息再修正重试，不要盲目重复
"""


@dataclass
class AlfredDeps:
    config: Config
    blocks: MemoryBlocks | None
    confirm: Callable[[str], bool] = lambda msg: False  # 默认拒绝
    last_recalled: list[str] = field(default_factory=list)  # 本轮召回的记忆（/why 用）
    session_id: str = ""
    bus: EventBus = field(default_factory=EventBus)
    tool_records: list[ToolCallRecord] = field(default_factory=list)
    tool_records_lock: threading.Lock = field(default_factory=threading.Lock)
    # 单轮工具调用次数，防止 agent 无限循环调用同一工具
    tool_call_count: int = 0
    # 单轮 code_patch 次数（上限 1，防止连环修改）
    code_patch_count: int = 0
    # 预加载缓存：技能索引 + 教训文本（build_agent 时一次性读好，避免每轮 I/O）
    skill_index: str = ""
    lessons_text: str = ""
    # 规则层：常驻规则 + 可召回规则索引，build_agent 时一次读好
    rules_text: str = ""


# 单轮工具调用硬上限
_MAX_TOOL_CALLS_PER_TURN = 20


@dataclass(frozen=True)
class ToolPipelineConfig:
    timeout_s: int = 60
    result_max_chars: int = 5_000
    truncate_suffix: str = "\n[输出已截断]"


class ToolExecutionPipeline:
    def __init__(self, config: ToolPipelineConfig):
        self.config = config

    def pre_check(self, ctx) -> None:
        if ctx.deps.tool_call_count >= _MAX_TOOL_CALLS_PER_TURN:
            raise ToolLimitExceeded

    def confirm_gate(self, ctx, tool_name: str, kwargs: dict) -> None:
        prompt = _confirm_prompt(tool_name, kwargs)
        if prompt is not None and not ctx.deps.confirm(prompt):
            raise ToolDeniedError("用户拒绝了该操作。")

    def execute_body(self, fn, ctx, kwargs) -> str:
        """执行工具函数。支持 timeout_s 超时控制。"""
        timeout = self.config.timeout_s
        if timeout is None or timeout <= 0:
            return fn(ctx, **kwargs)

        import threading as _th

        result_holder: list[str | Exception] = []
        exception_holder: list[BaseException] = []

        def _run() -> None:
            try:
                result_holder.append(fn(ctx, **kwargs))
            except Exception as exc:
                exception_holder.append(exc)

        thread = _th.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            return (
                f"错误：工具执行超时（{timeout} 秒限制）。"
                f"请拆分为更小的调用或增大 timeout_s。"
            )
        if exception_holder:
            raise exception_holder[0]
        return str(result_holder[0]) if result_holder else ""

    def post_normalize(self, result: str) -> str:
        if len(result) > self.config.result_max_chars:
            return result[:self.config.result_max_chars] + self.config.truncate_suffix
        return result


_default_pipeline = ToolExecutionPipeline(ToolPipelineConfig())


def _confirm_prompt(tool_name: str, args: dict) -> str | None:
    """Return a user-facing confirmation prompt, or None if no confirmation needed."""
    if tool_name == "shell":
        return f"管家想要执行命令：\n  {args.get('command', '')}\n允许吗？"
    if tool_name == "run_python":
        code = args.get("code", "")
        return f"管家想要运行 Python 代码：\n{code[:400]}\n允许吗？"
    if tool_name == "memory_update_block":
        name = args.get("name", "")
        content = args.get("content", "")
        reason = args.get("reason", "")
        preview = content[:300] + ("…" if len(content) > 300 else "")
        if name == "persona":
            return (
                "管家想要修改自己的人格设定（原因：%s）\n"
                "新内容预览：\n%s\n允许吗？" % (reason or "未说明", preview)
            )
        if name == "human":
            return (
                "管家想要修改对用户的认知画像（原因：%s）\n"
                "新内容预览：\n%s\n允许吗？" % (reason or "未说明", preview)
            )
    if tool_name == "code_patch":
        path = args.get("path", "")
        old = args.get("old_string", "")
        new = args.get("new_string", "")
        return (
            "管家想要修改自己的源代码（自举进化）：\n"
            "  文件：%s\n"
            "  旧代码（截取）：%s\n"
            "  新代码（截取）：%s\n"
            "允许吗？"
            % (path, old[:300], new[:300])
        )
    return None


def _wrap_tool(fn: Callable, tool_name: str) -> Callable:
    """Wrap a raw tool: lifecycle events + confirmation + pipeline execution."""

    @functools.wraps(fn)
    def wrapper(ctx: RunContext[AlfredDeps], **kwargs):
        session_id = ctx.deps.session_id
        bus = ctx.deps.bus
        records = ctx.deps.tool_records
        records_lock = ctx.deps.tool_records_lock

        try:
            _default_pipeline.pre_check(ctx)
        except ToolLimitExceeded:
            return (
                f"错误：本轮工具调用已达上限 {_MAX_TOOL_CALLS_PER_TURN}，"
                f"请精简思路后重试。"
            )

        ctx.deps.tool_call_count += 1
        bus.emit(ToolCallStart(session_id=session_id, tool_name=tool_name, args=kwargs, tool_call_id=ctx.tool_call_id))

        try:
            _default_pipeline.confirm_gate(ctx, tool_name, kwargs)
        except ToolDeniedError as exc:
            reason = exc.reason if exc.reason else "用户拒绝了该操作。"
            bus.emit(ToolDenied(
                session_id=session_id, tool_name=tool_name,
                args=kwargs, reason=reason, tool_call_id=ctx.tool_call_id
            ))
            bus.emit(ToolCallEnd(
                session_id=session_id, tool_name=tool_name,
                args=kwargs, result=reason, is_error=True,
                tool_call_id=ctx.tool_call_id
            ))
            with records_lock:
                records.append(ToolCallRecord(
                    tool_name=tool_name, args=kwargs, result=reason,
                    is_error=True, tool_call_id=ctx.tool_call_id
                ))
            return reason

        try:
            result = _default_pipeline.execute_body(fn, ctx, kwargs)
        except Exception as exc:
            error_text = str(exc)
            bus.emit(ToolCallEnd(
                session_id=session_id, tool_name=tool_name,
                args=kwargs, result=error_text, is_error=True,
                tool_call_id=ctx.tool_call_id
            ))
            with records_lock:
                records.append(ToolCallRecord(
                    tool_name=tool_name, args=kwargs,
                    result=error_text[:5000], is_error=True,
                    tool_call_id=ctx.tool_call_id
                ))
            return error_text

        normalized = _default_pipeline.post_normalize(result)

        bus.emit(ToolCallEnd(
            session_id=session_id, tool_name=tool_name,
            args=kwargs, result=normalized, is_error=False,
            tool_call_id=ctx.tool_call_id
        ))
        with records_lock:
            records.append(ToolCallRecord(
                tool_name=tool_name, args=kwargs,
                result=str(normalized)[:5000], is_error=False,
                tool_call_id=ctx.tool_call_id
            ))
        return normalized

    return wrapper


def build_agent(config: Config, model_ref: str | None = None) -> Agent[AlfredDeps, str]:
    """组装主 agent：模型 + 三层 prompt + 恒定工具集。"""
    agent: Agent[AlfredDeps, str] = Agent(
        build_model(config, model_ref or config.models.chat),
        deps_type=AlfredDeps,
        instructions=INSTRUCTIONS,
    )

    # ── ② 半静态层：memory blocks（内容会变，但位置固定）────────────

    @agent.system_prompt
    def inject_persona(ctx: RunContext[AlfredDeps]) -> str:
        blocks = ctx.deps.blocks
        persona = blocks.read("persona") if blocks is not None else ""
        return f"# 你的人格设定（persona）\n{persona}"

    @agent.system_prompt
    def inject_human(ctx: RunContext[AlfredDeps]) -> str:
        blocks = ctx.deps.blocks
        human = blocks.read("human") if blocks is not None else ""
        return f"# 你对用户的认知（human）\n{human}"

    # ── ④ 静态缓存：技能索引（会话内不变，build_agent 时一次读好）────
    # ⑤ 动态缓存：匹配 skill 全文（每轮基于用户输入重新匹配）

    @agent.system_prompt
    def inject_skills(ctx: RunContext[AlfredDeps]) -> str:
        return ctx.deps.skill_index

    @agent.system_prompt
    def inject_lessons(ctx: RunContext[AlfredDeps]) -> str:
        return ctx.deps.lessons_text

    # ── ③ 动态层：规则、日期（易变信息放最后）──────────────────

    @agent.system_prompt
    def inject_rules(ctx: RunContext[AlfredDeps]) -> str:
        # 规则索引在 build_agent 时已经扫描渲染好，直接返回缓存，
        # 避免每轮对话都触发磁盘扫描（rules 目录膨胀时此处线性增长）。
        return ctx.deps.rules_text

    @agent.system_prompt
    def inject_date(ctx: RunContext[AlfredDeps]) -> str:
        return f"当前日期：{datetime.now():%Y-%m-%d}"

    # ── 恒定工具集 ──────────────────────────────────────────────────

    def memory_search(ctx: RunContext[AlfredDeps], query: str) -> str:
        """在长期记忆中搜索关于用户的事实（偏好、经历、计划等）。
        当你需要回忆用户说过的话、了解用户某方面情况时使用。"""
        memories = recall.recall(ctx.deps.config, query)
        ctx.deps.last_recalled = [m.get("memory", str(m)) for m in memories]
        return recall.render_for_prompt(memories)

    def memory_update_block(ctx: RunContext[AlfredDeps], name: str, content: str,
                            reason: str = "") -> str:
        """更新核心记忆块（human=对用户的认知 / persona=自己的人格设定）。
        content 为整块新内容（含原内容的基础上修改），不是追加。
        修改 persona 需要用户确认。"""
        blocks = ctx.deps.blocks
        if blocks is None:
            return "错误：记忆块未初始化，无法更新。"
        return blocks.update(name, content, reason)

    def notes_search(ctx: RunContext[AlfredDeps], query: str, limit: int = 5) -> str:
        """在用户的个人笔记库中搜索相关内容。返回带出处（文件与章节）的片段。
        当用户的问题可能与其笔记有关时使用。"""
        from .knowledge.ingest import search_notes

        try:
            results = search_notes(ctx.deps.config, query, limit=limit)
        except Exception as e:
            return f"笔记库暂不可用（{e}）。如果用户尚未运行 alfred ingest 索引笔记，请提示 TA。"
        if not results:
            return "笔记库中没有找到相关内容（可能尚未索引，可提示用户运行 alfred ingest）。"
        lines = []
        for r in results:
            lines.append(f"【出处：{r['source']}】\n{r['text']}")
        return "\n\n---\n\n".join(lines)

    def episodes_search(ctx: RunContext[AlfredDeps], query: str, limit: int = 3) -> str:
        """检索管家过去的成功经验（情景记忆）。
        适用于用户问「你之前是怎么处理 X 的」「上次遇到类似情况怎么做的」，
        或者你正在面对一个和过去类似的场景、希望借鉴之前的做法。"""
        from .memory import episodic

        try:
            results = episodic.search_episodes(ctx.deps.config, query, limit=limit)
        except Exception as e:
            return f"情景记忆库暂不可用（{e}）。"
        if not results:
            return "情景记忆库中暂时没有匹配的成功案例。"
        lines = []
        for r in results:
            situation = r.get("situation", "")
            thoughts = r.get("thoughts", "")
            action = r.get("action", "")
            result = r.get("result", "")
            lines.append(
                f"**场景**：{situation}\n"
                f"**思路**：{thoughts}\n"
                f"**行动**：{action}\n"
                f"**结果**：{result}"
            )
        return "\n\n---\n\n".join(lines)

    def file_read(ctx: RunContext[AlfredDeps], path: str) -> str:
        """读取一个文本文件。用于激活技能（读取 SKILL.md）、读取规则文件、
        查看用户指定的文件等。"""
        p = Path(path).expanduser()
        if not p.is_file():
            return f"错误：文件不存在：{p}。请检查路径（技能/规则文件路径见索引中的「文件：」字段）。"
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"错误：{p} 不是文本文件，无法读取。"
        if len(text) > 20_000:
            full_len = len(text)
            text = text[:20_000] + f"\n\n[已截断：完整文件 {full_len} 字符，路径 {p}]"
        return text

    # ── 对话内目标状态（goal）────────────────────────────────────────
    # 让 agent 感知自己正在做什么、进度如何、是否受阻

    def create_goal(ctx: RunContext[AlfredDeps], description: str) -> str:
        """建立当前会话的目标。

        接手跨多轮的长任务（读一本书、写一个功能、规划一个项目）时用此工具
        记录目标，后续用 update_goal 更新进度或状态。"""
        from .goals import create_goal as do_create
        result = do_create(ctx.deps.config, ctx.deps.session_id, description)
        return result["message"]

    def update_goal(ctx: RunContext[AlfredDeps], *,
                    status: str | None = None,
                    description: str | None = None,
                    progress: str | None = None,
                    block_reason: str | None = None) -> str:
        """更新当前会话的目标状态。

        - 进度有进展：progress=\"已完成 XXX\"
        - 遇阻：status=\"blocked\" + block_reason
        - 完成：status=\"completed\"
        - 取消：status=\"cleared\"
        """
        from .goals import update_goal as do_update
        result = do_update(
            ctx.deps.config, ctx.deps.session_id,
            status=status, description=description,
            progress=progress, block_reason=block_reason,
        )
        return result["message"]

    def get_goal(ctx: RunContext[AlfredDeps]) -> str:
        """查看当前会话的目标状态。"""
        from .goals import get_goal as do_get
        state = do_get(ctx.deps.config, ctx.deps.session_id)
        if state is None:
            return "当前没有活跃目标。"
        lines = [f"目标：{state['description']}"]
        lines.append(f"状态：{state['status']}")
        if state.get("progress"):
            lines.append(f"进度：{state['progress']}")
        if state.get("block_reason"):
            lines.append(f"阻塞原因：{state['block_reason']}")
        return "\n".join(lines)

    # ── 会话历史自查 ─────────────────────────────────────────────────
    # 长会话中 agent 会"忘记"前面的工具输出，提供回查能力

    def session_search(ctx: RunContext[AlfredDeps], query: str,
                       limit: int = 5) -> str:
        """搜索当前会话历史中的消息内容。

        适用于长会话中需要回想自己之前做过什么、用过什么工具、
        得到过什么结论时使用。"""
        from .history import Session
        try:
            session = Session(ctx.deps.config, session_id=ctx.deps.session_id)
        except Exception as e:
            return f"会话历史暂不可用（{e}）。"
        keywords = [k for k in query.lower().split() if k]
        matches: list[str] = []
        for msg in reversed(session.messages):
            content = msg.content
            if content and all(k in content.lower() for k in keywords):
                tag = f"[{msg.role}]"
                if msg.name:
                    tag += f"/{msg.name}"
                matches.append(f"{tag} {content[:500]}")
                if len(matches) >= limit:
                    break
        if not matches:
            return f"会话历史中没有找到与「{query}」相关的内容。"
        return (
            f"在会话历史中找到 {len(matches)} 条匹配（搜索词：{query}）：\n"
            + "\n\n---\n\n".join(matches)
        )

    # ── 喂书↔聊天打通：召回 frameworks ───────────────────────────────
    # 用户喂过的书在聊天中应该被主动引用

    def frameworks_search(ctx: RunContext[AlfredDeps], query: str,
                          limit: int = 3) -> str:
        """搜索用户喂书提炼的思维框架卡片。

        当用户聊到的话题可能与其之前喂过的书、文章有关时主动使用。
        例如用户聊到「决策」「认知偏差」时，搜一下看用户是否喂过相关书籍。"""
        from .knowledge.feed import search_frameworks
        try:
            results = search_frameworks(ctx.deps.config, query, limit=limit)
        except Exception as e:
            return f"框架库暂不可用（{e}）。"
        if not results:
            return "思维框架库中没有找到相关内容。"
        lines: list[str] = []
        for r in results:
            title = r.get("title", "")
            source = r.get("source", "")
            text = r.get("text", r.get("content", ""))
            lines.append(f"【{title}】（来源：{source}）\n{text[:800]}")
        return "\n\n---\n\n".join(lines)

    # ── Agent 自调度 ─────────────────────────────────────────────────
    # 让 agent 能在对话中给自己定闹钟

    def schedule_create(ctx: RunContext[AlfredDeps], description: str,
                        prompt: str, due_at: str | None = None) -> str:
        """创建一个定时提醒任务。

        Args:
            description: 任务描述（给用户看）
            prompt: 到期时注入 agent 的提示（用第一人称，agent 视角）
            due_at: 到期时间，格式 YYYY-MM-DD HH:MM。不传默认 24 小时后。
        """
        from .schedule import schedule_create as do_create
        from datetime import datetime as _dt
        if due_at:
            try:
                parsed = _dt.strptime(due_at, "%Y-%m-%d %H:%M")
                now_tz = _dt.now().astimezone().tzinfo
                parsed = parsed.replace(tzinfo=now_tz)
                timestamp = parsed.timestamp()
            except (ValueError, TypeError):
                return "错误：due_at 格式应为 YYYY-MM-DD HH:MM"
            result = do_create(ctx.deps.config, ctx.deps.session_id,
                               description, prompt, due_at=timestamp)
        else:
            result = do_create(ctx.deps.config, ctx.deps.session_id,
                               description, prompt)
        return result["message"]

    def schedule_delete(ctx: RunContext[AlfredDeps], schedule_id: str) -> str:
        """取消一条定时任务。id 从 schedule_list 获取。"""
        from .schedule import schedule_delete as do_delete
        result = do_delete(ctx.deps.config, schedule_id)
        return result["message"]

    def schedule_list(ctx: RunContext[AlfredDeps]) -> str:
        """列出所有定时任务。"""
        from .schedule import schedule_list as do_list
        result = do_list(ctx.deps.config, ctx.deps.session_id)
        if result["count"] == 0:
            return "当前没有定时任务。"
        lines = [f"共有 {result['count']} 条定时任务："]
        for e in result["entries"]:
            lines.append(
                f"  [{e['id']}] {e['description']}  "
                f"到期：{e['due_at']}  ({e['status']})"
            )
        return "\n".join(lines)

    def shell(ctx: RunContext[AlfredDeps], command: str) -> str:
        """执行 shell 命令（需用户确认）。用于文件操作、运行脚本等。"""
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=60
            )
            out = (proc.stdout + proc.stderr).strip()
            if len(out) > 5_000:
                out = out[:5_000] + "\n[输出已截断]"
            return out or f"（命令执行完毕，无输出，退出码 {proc.returncode}）"
        except subprocess.TimeoutExpired:
            return "错误：命令执行超时（60 秒限制）。请拆分为更小的命令。"

    def run_python(ctx: RunContext[AlfredDeps], code: str) -> str:
        """执行一段 Python 代码并返回 stdout（需用户确认）。
        适合数据处理、计算、格式转换等确定性任务——用代码而非逐字生成。"""
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
            )
            out = (proc.stdout + proc.stderr).strip()
            if len(out) > 5_000:
                out = out[:5_000] + "\n[输出已截断]"
            return out or "（执行完毕，无输出）"
        except subprocess.TimeoutExpired:
            return "错误：代码执行超时（60 秒限制）。"

    def code_patch(
        ctx: RunContext[AlfredDeps],
        path: str,
        old_string: str,
        new_string: str,
    ) -> str:
        """精确替换项目源代码中的一段文本（自举进化工具）。

        理论依据：CodeAct (ICML 2024) + SWE-bench 评估范式。
        人类是进化方向决策者，agent 是执行者。

        三重门禁：
        - 路径门禁：只允许修改 alfred/ 和 config.yaml
        - 语法门禁：Python 文件修改后 py_compile 验证
        - 测试门禁：修改后跑 pytest，不过则自动回滚

        单轮最多调用一次 code_patch，防止连环修改。
        """
        if ctx.deps.code_patch_count >= 1:
            return "错误：本轮已调用过 code_patch，一次最多修改一个文件。"
        ctx.deps.code_patch_count += 1

        from .codewriting import code_patch as do_patch

        return do_patch(path, old_string, new_string)

    # ── 情景记忆写入 ────────────────────────────────────────────────
    # 让 agent 在成功完成任务后主动把经验沉淀为四元组，补全情景记忆

    def save_episode(ctx: RunContext[AlfredDeps], situation: str,
                     thoughts: str, action: str, result: str) -> str:
        """记录一条成功的处理案例（情景记忆四元组）。

        当你完成了一个复杂/有代表性的任务（成功调试了一个 bug、给用户提出了
        一个被采纳的方案、解决了某个棘手问题），用此工具保存为情景记忆，
        下次遇到类似场景 agent 会自动检索借鉴。

        Args:
            situation: 当时面对的场景 / 用户想要什么
            thoughts: 你怎样分析问题、关键判断
            action: 具体采取了哪些步骤
            result: 最终效果 / 用户反馈
        """
        from .memory.episodic import Episode, save_episode as do_save

        ep = Episode(
            situation=situation, thoughts=thoughts,
            action=action, result=result,
        )
        try:
            ep_id = do_save(ctx.deps.config, ep)
            return f"情景记忆已保存 [{ep_id}]：{situation[:50]}"
        except Exception as e:
            return f"情景记忆保存失败：{e}"

    agent.tool(_wrap_tool(memory_search, "memory_search"))
    agent.tool(_wrap_tool(memory_update_block, "memory_update_block"))
    agent.tool(_wrap_tool(notes_search, "notes_search"))
    agent.tool(_wrap_tool(episodes_search, "episodes_search"))
    agent.tool(_wrap_tool(save_episode, "save_episode"))
    agent.tool(_wrap_tool(file_read, "file_read"))
    agent.tool(_wrap_tool(create_goal, "create_goal"))
    agent.tool(_wrap_tool(update_goal, "update_goal"))
    agent.tool(_wrap_tool(get_goal, "get_goal"))
    agent.tool(_wrap_tool(session_search, "session_search"))
    agent.tool(_wrap_tool(frameworks_search, "frameworks_search"))
    agent.tool(_wrap_tool(schedule_create, "schedule_create"))
    agent.tool(_wrap_tool(schedule_delete, "schedule_delete"))
    agent.tool(_wrap_tool(schedule_list, "schedule_list"))
    agent.tool(_wrap_tool(shell, "shell"))
    agent.tool(_wrap_tool(run_python, "run_python"))
    agent.tool(_wrap_tool(code_patch, "code_patch"))

    return agent


def _load_history(session: Session) -> list | None:
    """恢复 LLM 消息历史：优先原生状态，否则用文本记录做种子上下文。"""
    if session.llm_state:
        try:
            return ModelMessagesTypeAdapter.validate_json(session.llm_state)
        except Exception:
            pass
    return None


def chat_turn_stream(
    agent: Agent[AlfredDeps, str],
    deps: AlfredDeps,
    session: Session,
    user_input: str,
    bus: EventBus | None = None,
) -> Iterator[Event]:
    """一轮对话：压缩检查 → 流式运行 → 持久化。  以事件流形式返回。"""
    config = deps.config
    summary = maybe_compact(config, session)

    history = _load_history(session)
    prompt = user_input
    if history is None and session.messages:
        # 无原生状态（新会话/压缩后）：用归一化记录做种子
        prompt = (
            "以下是我们之前的对话记录（供你恢复上下文）：\n\n"
            f"{session.transcript()}\n\n---\n\n用户现在说：{user_input}"
        )

    bus = bus or EventBus()
    queue: Queue[Event | None] = Queue()
    bus.subscribe(queue.put)

    turn_deps = AlfredDeps(
        config=deps.config,
        blocks=deps.blocks,
        confirm=deps.confirm,
        last_recalled=deps.last_recalled,
        session_id=session.id,
        bus=bus,
        tool_records=[],
        code_patch_count=0,
        skill_index=deps.skill_index,
        lessons_text=deps.lessons_text,
        rules_text=deps.rules_text,
    )

    error_holder: list[Exception] = []

    def _run() -> None:
        try:
            bus.emit(TurnStart(session_id=session.id, user_text=user_input))
            if summary:
                bus.emit(
                    ContextCompacted(
                        session_id=session.id,
                        summary=summary,
                        retained_message_count=len(session.messages),
                    )
                )

            current_history = history
            user_prompt = prompt
            output = ""
            final_result = None

            with agent.parallel_tool_call_execution_mode("parallel"):
                while True:
                    result = agent.run_stream_sync(
                        user_prompt, deps=turn_deps, message_history=current_history
                    )
                    final_result = result
                    for delta in result.stream_text(delta=True):
                        if delta:
                            bus.emit(
                                AssistantChunk(session_id=session.id, delta=delta)
                            )
                    output += result.get_output()

                    messages = result.all_messages()
                    last_response = next(
                        (m for m in reversed(messages) if isinstance(m, ModelResponse)),
                        None,
                    )
                    has_tool_calls = (
                        last_response is not None
                        and any(
                            isinstance(p, ToolCallPart) for p in last_response.parts
                        )
                    )
                    if not has_tool_calls:
                        break
                    current_history = messages
                    user_prompt = None

            deps.last_recalled = turn_deps.last_recalled
            session.add_user(user_input)
            session.add_assistant(output, tool_calls=list(turn_deps.tool_records))
            session.set_llm_state(final_result.all_messages_json())
            bus.emit(
                TurnEnd(
                    session_id=session.id,
                    usage=asdict(final_result.usage) if final_result.usage else None,
                )
            )

        except Exception as exc:
            error_holder.append(exc)
            bus.emit(TurnError(session_id=session.id, error=str(exc)))
        finally:
            queue.put(None)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    while True:
        event = queue.get()
        if event is None:
            break
        yield event
    thread.join()
    if error_holder:
        raise error_holder[0]


def chat_turn(
    agent: Agent[AlfredDeps, str], deps: AlfredDeps, session: Session, user_input: str
) -> str:
    """Backward-compatible synchronous wrapper that drains the event stream.

    Returns the assistant's final text output.
    """
    output_parts: list[str] = []
    compacted = False
    for event in chat_turn_stream(agent, deps, session, user_input):
        if isinstance(event, AssistantChunk):
            output_parts.append(event.delta)
        elif isinstance(event, ContextCompacted):
            compacted = True
    reply = "".join(output_parts)
    if compacted:
        return f"（上下文已自动压缩）\n\n{reply}"
    return reply
