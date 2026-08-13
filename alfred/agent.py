"""Agent 内核：prompt 分层组装 + 恒定工具集 + 对话循环。

设计依据：
- prompt 三层组装（Manus KV-cache 纪律）：静态 instructions → 半静态
  memory blocks → 动态层（skills/rules 索引、日期）
- 工具集会话内恒定（mask 不删）；按工作流合并；命名空间前缀；
  错误返回可操作化（Anthropic《Writing tools for agents》）
- persona 修改、shell/python 执行必须用户确认——代码层强制（hooks 思想），
  不靠 prompt 约束
- run_python 作为 CodeAct 逃生舱
"""

from __future__ import annotations

import functools
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
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


def _confirm_prompt(tool_name: str, args: dict) -> str | None:
    """Return a user-facing confirmation prompt, or None if no confirmation needed."""
    if tool_name == "shell":
        return f"管家想要执行命令：\n  {args.get('command', '')}\n允许吗？"
    if tool_name == "run_python":
        code = args.get("code", "")
        return f"管家想要运行 Python 代码：\n{code[:400]}\n允许吗？"
    if tool_name == "memory_update_block" and args.get("name") == "persona":
        content = args.get("content", "")
        reason = args.get("reason", "")
        return (
            f"管家想要修改自己的人格设定（原因：{reason or '未说明'}）\n"
            f"新内容预览：\n{content[:300]}{'…' if len(content) > 300 else ''}\n允许吗？"
        )
    return None


def _wrap_tool(fn: Callable, tool_name: str) -> Callable:
    """Wrap a raw tool so it emits lifecycle events and handles confirmation uniformly."""

    @functools.wraps(fn)
    def wrapper(ctx: RunContext[AlfredDeps], **kwargs):
        session_id = ctx.deps.session_id
        bus = ctx.deps.bus
        records = ctx.deps.tool_records
        records_lock = ctx.deps.tool_records_lock

        bus.emit(ToolCallStart(session_id=session_id, tool_name=tool_name, args=kwargs, tool_call_id=ctx.tool_call_id))

        prompt = _confirm_prompt(tool_name, kwargs)
        if prompt is not None and not ctx.deps.confirm(prompt):
            reason = "用户拒绝了该操作。"
            bus.emit(
                ToolDenied(
                    session_id=session_id,
                    tool_name=tool_name,
                    args=kwargs,
                    reason=reason,
                    tool_call_id=ctx.tool_call_id,
                )
            )
            bus.emit(
                ToolCallEnd(
                    session_id=session_id,
                    tool_name=tool_name,
                    args=kwargs,
                    result=reason,
                    is_error=True,
                    tool_call_id=ctx.tool_call_id,
                )
            )
            with records_lock:
                records.append(
                    ToolCallRecord(
                        tool_name=tool_name,
                        args=kwargs,
                        result=reason,
                        is_error=True,
                        tool_call_id=ctx.tool_call_id,
                    )
                )
            return reason

        try:
            result = fn(ctx, **kwargs)
        except Exception as exc:
            error_text = str(exc)
            bus.emit(
                ToolCallEnd(
                    session_id=session_id,
                    tool_name=tool_name,
                    args=kwargs,
                    result=error_text,
                    is_error=True,
                    tool_call_id=ctx.tool_call_id,
                )
            )
            with records_lock:
                records.append(
                    ToolCallRecord(
                        tool_name=tool_name,
                        args=kwargs,
                        result=error_text[:5000],
                        is_error=True,
                        tool_call_id=ctx.tool_call_id,
                    )
                )
            return error_text

        bus.emit(
            ToolCallEnd(
                session_id=session_id,
                tool_name=tool_name,
                args=kwargs,
                result=result,
                is_error=False,
                tool_call_id=ctx.tool_call_id,
            )
        )
        with records_lock:
            records.append(
                ToolCallRecord(
                    tool_name=tool_name,
                    args=kwargs,
                    result=str(result)[:5000],
                    is_error=False,
                    tool_call_id=ctx.tool_call_id,
                )
            )
        return result

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

    # ── ③ 动态层：规则、技能索引、日期（易变信息放最后）──────────────

    @agent.system_prompt
    def inject_rules(ctx: RunContext[AlfredDeps]) -> str:
        always, index = render_rules(scan_rules(ctx.deps.config))
        return "\n\n".join(t for t in (always, index) if t)

    @agent.system_prompt
    def inject_skills(ctx: RunContext[AlfredDeps]) -> str:
        return render_skills_index(scan_skills(ctx.deps.config))

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
            return text[:20_000] + f"\n\n[已截断：完整文件 {len(text)} 字符，路径 {p}]"
        return text

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

    agent.tool(_wrap_tool(memory_search, "memory_search"))
    agent.tool(_wrap_tool(memory_update_block, "memory_update_block"))
    agent.tool(_wrap_tool(notes_search, "notes_search"))
    agent.tool(_wrap_tool(file_read, "file_read"))
    agent.tool(_wrap_tool(shell, "shell"))
    agent.tool(_wrap_tool(run_python, "run_python"))

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
