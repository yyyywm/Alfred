"""上下文压缩：丢内容留指针 + 用户偏好最高保留优先级。

设计依据：
- Manus：可恢复压缩——工具输出裁剪为指针引用（文件路径/笔记 ID 还在就能找回）
- Chroma context-rot 实证：塞进无关上下文迫使模型同时做检索+推理，必须裁剪
- Claude Code：蒸馏摘要时保留架构决策/未解决问题/用户偏好，丢弃冗余工具输出
- append-only 纪律的例外：压缩发生在会话过长时，压缩结果作为新的会话起点
"""

from __future__ import annotations

from pydantic_ai import Agent

from .config import Config
from .history import Message, Session
from .llm import build_model

# 触发压缩的粗略阈值（按字符估算，~4 字符/token）
COMPACT_THRESHOLD_CHARS = 60_000
# 压缩后保留的最近消息条数（不参与蒸馏，原样保留）
KEEP_RECENT_MESSAGES = 10
# 单条工具输出超过此长度即裁剪为指针
TOOL_OUTPUT_MAX_CHARS = 2_000

DISTILL_INSTRUCTIONS = """你是会话压缩器。把一段对话历史蒸馏为高密度摘要，供后续会话续跑。

保留优先级（从高到低）：
1. 用户透露的个人信息、偏好、决策、明确要求（最高优先级，逐条保留）
2. 进行中的任务、未解决的问题、用户的目标
3. 关键事实与结论
4. 涉及的资源指针（文件路径、笔记名、URL）

丢弃：寒暄、重复内容、冗长的工具输出细节（只留"查了什么、结论是什么"）。
输出格式：分节的要点列表，不超过 800 字。直接输出摘要，不要任何前言。"""


def _session_chars(session: Session) -> int:
    return sum(len(m.content) for m in session.messages)


def trim_tool_outputs(session: Session) -> int:
    """把超长工具输出裁剪为指针引用（可恢复压缩）。返回裁剪条数。"""
    n = 0
    for m in session.messages:
        if m.role == "tool" and not m.compacted and len(m.content) > TOOL_OUTPUT_MAX_CHARS:
            head = m.content[:500]
            m.content = (
                f"{head}\n\n[已裁剪：原输出 {len(m.content)} 字符。"
                f"如需完整内容可重新调用工具 {m.name or ''} 获取]"
            )
            m.compacted = True
            n += 1
    return n


def maybe_compact(config: Config, session: Session) -> str | None:
    """超过阈值时蒸馏旧消息。返回压缩摘要（无压缩返回 None）。

    策略：保留最近 KEEP_RECENT_MESSAGES 条原样消息，更早的蒸馏为一条摘要消息。
    """
    trim_tool_outputs(session)
    if _session_chars(session) < COMPACT_THRESHOLD_CHARS:
        return None

    old = session.messages[:-KEEP_RECENT_MESSAGES]
    recent = session.messages[-KEEP_RECENT_MESSAGES:]
    if not old:
        return None

    transcript = "\n".join(
        f"[{m.role}{'/' + m.name if m.name else ''}] {m.content[:1000]}" for m in old
    )
    agent = Agent(build_model(config, config.models.memory_write),
                  instructions=DISTILL_INSTRUCTIONS)
    summary = agent.run_sync(
        f"请蒸馏以下对话历史（{len(old)} 条消息）：\n\n{transcript}"
    ).output

    # 用摘要替换旧消息并整体重写会话文件（llm_state 作废，回退种子上下文）
    session.messages = [
        Message(role="assistant",
                content=f"[早前对话摘要]\n{summary}", compacted=True),
        *recent,
    ]
    session.rewrite()
    return summary
