"""自监控度量：从对话历史中统计工具调用、记忆召回、skill 匹配的趋势数据。

数据来源：
- 工具调用：每个会话 JSONL 中 assistant 消息的 tool_calls（含 is_error）
- 记忆召回：无显式日志；用 assistant 消息中"没有召回相关记忆"的反向统计做近似
- skill 匹配：无 SkillSuggested 事件日志；用 skill 索引扫描 + 对话中 file_read
  读取 skill 目录的次数做近似

设计原则：
- 只读，不修改任何数据
- 输出结构化 dict，便于后续可视化 / 告警接入
- 时间范围参数可配置（默认 90 天）
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from ..history import Session, list_sessions


def collect_metrics(config, days: int = 90) -> dict:
    """收集自监控指标。"""
    now = __import__("time").time()
    cutoff = now - days * 86400

    # 按天分组
    daily_tool_calls: Counter = Counter()
    daily_tool_errors: Counter = Counter()
    daily_sessions: Counter = Counter()
    daily_turns: Counter = Counter()
    tool_name_total: Counter = Counter()
    tool_name_errors: Counter = Counter()
    memory_recall_hits: int = 0
    memory_recall_misses: int = 0

    # skill 被 file_read 调用的次数
    skill_file_reads: Counter = Counter()
    skill_dir = config.path(config.paths.skills_dirs[0]) if config.paths.skills_dirs else None
    skill_dir_str = str(skill_dir).lower() if skill_dir else ""

    for sid, mtime, _count in list_sessions(config):
        if mtime < cutoff:
            continue
        try:
            s = Session(config, session_id=sid)
        except Exception:
            continue

        day_key = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d")
        daily_sessions[day_key] += 1
        daily_turns[day_key] += len(s.messages)

        for msg in s.messages:
            if msg.role == "assistant":
                # 记忆召回：从文本判断（近似）
                text = (msg.content or "").lower()
                if "没有召回相关记忆" in text:
                    memory_recall_misses += 1
                elif "memory_search" in text or any(tc.tool_name == "memory_search" for tc in msg.tool_calls or []):
                    memory_recall_hits += 1

                # 工具调用
                for tc in msg.tool_calls or []:
                    daily_tool_calls[day_key] += 1
                    tool_name_total[tc.tool_name] += 1
                    if tc.is_error:
                        daily_tool_errors[day_key] += 1
                        tool_name_errors[tc.tool_name] += 1

                    # skill 匹配：file_read 读取 skill 目录
                    if tc.tool_name == "file_read":
                        args = tc.args or {}
                        path = str(args.get("path", "")).lower()
                        if skill_dir_str and skill_dir_str in path:
                            # 提取 skill 名
                            try:
                                skill_name = Path(path).parent.name
                                skill_file_reads[skill_name] += 1
                            except Exception:
                                pass

    total_calls = sum(daily_tool_calls.values())
    total_errors = sum(daily_tool_errors.values())
    days_with_data = len(daily_tool_calls)

    return {
        "days": days,
        "total_sessions": len(daily_sessions),
        "total_turns": sum(daily_turns.values()),
        "days_with_activity": days_with_data,
        "tool_calls": {
            "total": total_calls,
            "errors": total_errors,
            "success_rate": round(1 - total_errors / total_calls, 4) if total_calls else None,
            "daily": {k: daily_tool_calls[k] for k in sorted(daily_tool_calls)},
            "by_name": dict(tool_name_total.most_common()),
            "errors_by_name": dict(tool_name_errors.most_common()),
        },
        "memory_recall": {
            "hits": memory_recall_hits,
            "misses": memory_recall_misses,
            "hit_rate": (
                round(memory_recall_hits / (memory_recall_hits + memory_recall_misses), 4)
                if (memory_recall_hits + memory_recall_misses) > 0 else None
            ),
        },
        "skill_usage": {
            "file_reads": dict(skill_file_reads.most_common()),
            "total_file_reads": sum(skill_file_reads.values()),
        },
        "daily_turns": {k: daily_turns[k] for k in sorted(daily_turns)},
        "daily_sessions": {k: daily_sessions[k] for k in sorted(daily_sessions)},
    }