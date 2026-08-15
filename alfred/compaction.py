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
# 单条工具输出超过此长度即裁剪为指针（兜底阈值）
TOOL_OUTPUT_MAX_CHARS = 2_000
# model-free 预处理：结构化工具保留头/尾的字符数
_PRUNE_HEAD_CHARS = 200
_PRUNE_TAIL_CHARS = 500
# 这些工具的输出必须完整保留（诊断关键，不可裁剪）
_PRUNE_PRESERVE_WHITELIST = {"code_patch"}
# 这些工具的输出按结构化修剪（head + 关键行 + 尾部）
_PRUNE_STRUCTURED = {"shell", "run_python"}
# 语义关键行：这些行即使不在错误范围内也保留，帮助压缩后仍能判断语义
_PRUNE_SEMANTIC_MARKERS = (
    "passed", "failed", "error:", "warning:", "skip", "skipped",
    "assertion", "traceback", "exception", "timeout", "killed",
    "::", "=====", "---", ">> ", "$ ",
)


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


def _extract_error_lines(text: str) -> str:
    """从多行文本中提取关键行（错误 + 语义关键行）。

    错误行：含 error / traceback / failed / exception / assertionerror / warning
    语义行：含 passed / skip / :: / ===== 等（pytest 等工具关键输出）
    返回首 5 + 尾 5 去重，防止异常栈过长。
    """
    error_kw = ("error", "traceback", "failed", "exception",
                "assertionerror", "warning")
    semantic_kw = _PRUNE_SEMANTIC_MARKERS
    hits: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().lower()
        if any(kw in stripped for kw in error_kw) or \
           any(kw in stripped for kw in semantic_kw):
            hits.append(line)
    if not hits:
        return ""
    head = hits[:5]
    tail = hits[-5:] if len(hits) > 10 else []
    return "\n".join(head + tail)


def prune_tool_result(tool_name: str | None, content: str) -> tuple[str, int]:
    """对单条工具结果做 model-free 结构化修剪。

    返回 (trimmed_text, chars_saved)。策略优先级：
    1. 白名单工具（code_patch 等）→ 原样保留
    2. 结构化工具（shell / run_python）→ head + error_lines + tail
    3. 其他 → 兜底截断到 _PRUNE_TAIL_CHARS
    """
    if not content:
        return content, 0

    original_len = len(content)

    if tool_name in _PRUNE_PRESERVE_WHITELIST:
        return content, 0

    if tool_name in _PRUNE_STRUCTURED:
        head = content[:_PRUNE_HEAD_CHARS]
        errors = _extract_error_lines(content)
        tail = content[-_PRUNE_TAIL_CHARS:] if (
            len(content) > _PRUNE_HEAD_CHARS + _PRUNE_TAIL_CHARS
        ) else ""
        parts = [head]
        if errors:
            parts.append(f"\n\n[错误/异常摘要]\n{errors}")
        if tail and tail != head:
            parts.append(f"\n\n[输出尾部]\n{tail}")
        trimmed = "\n".join(parts)
        if len(trimmed) > TOOL_OUTPUT_MAX_CHARS:
            trimmed = trimmed[:TOOL_OUTPUT_MAX_CHARS] + "\n[进一步裁剪]"
        return trimmed, original_len - len(trimmed)

    # 兜底：非结构化工具直接截断
    if len(content) <= _PRUNE_TAIL_CHARS:
        return content, 0
    trimmed = content[:_PRUNE_TAIL_CHARS] + f"\n[已截断：原 {original_len} 字符]"
    return trimmed, original_len - len(trimmed)


def trim_tool_outputs(session: Session) -> int:
    """对每条 tool 消息做 model-free 结构化修剪，超长再兜底截断。

    两步策略：
    1. 按工具类型做结构化修剪（保留头/错误/尾，白名单保留）
    2. 修剪后仍超 TOOL_OUTPUT_MAX_CHARS 的兜底截断为指针
    返回第 2 步触发的裁剪条数。
    """
    n = 0
    for m in session.messages:
        if m.role != "tool" or m.compacted:
            continue
        pruned, _ = prune_tool_result(m.name or None, m.content)
        m.content = pruned
        if len(m.content) > TOOL_OUTPUT_MAX_CHARS:
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
        f"[{m.role}{'/' + m.name if m.name else ''}] {m.content}" for m in old
    )
    agent = Agent(build_model(config, config.models.memory_write),
                  instructions=DISTILL_INSTRUCTIONS)
    summary = agent.run_sync(
        f"请蒸馏以下对话历史（{len(old)} 条消息）：\n\n{transcript}"
    ).output

    session.messages = [
        Message(role="assistant",
                content=f"[早前对话摘要]\n{summary}", compacted=True),
        *recent,
    ]
    session.rewrite()
    return summary