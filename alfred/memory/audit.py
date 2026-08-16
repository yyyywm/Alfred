"""记忆审计视图：诊断记忆库的健康度、召回有效性、工具调用趋势、冷笔记/死规则。

设计要点：
- 只读分析，不修改任何数据
- 基于已有数据源（history/*.jsonl / longterm / lessons / notes / rules / schedules / goals）
- 输出 JSON 报告 + 富文本诊断

数据来源与限制：
- 工具调用成功率：从历史消息的 tool_calls / is_error 统计
- 记忆写入数量：mem0 的 list_all 长度
- 冷笔记 / 死规则：无显式访问日志，用「写入后多久被 recall/ingest 引用过」做粗粒度估计
  （目前笔记索引没有访问时间戳，暂以"从未被 recall 命中的 source"作为冷笔记的代理）
- skill 匹配命中率：SkillSuggested 事件不在历史中，用 skills scan + file_read 工具调用频次做近似
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from ..history import Session, list_sessions
from . import longterm
from .lessons import LessonsBlock


class MemoryAuditReport:
    """记忆审计结果。"""

    def __init__(self) -> None:
        self.generated_at: float = time.time()
        self.total_sessions: int = 0
        self.total_messages: int = 0
        self.total_turns: int = 0

        # 记忆
        self.memory_count: int = 0
        self.memory_list: list[dict] = []
        self.lesson_count: int = 0
        self.lesson_categories: Counter = Counter()

        # 工具调用
        self.tool_calls_total: int = 0
        self.tool_success: int = 0
        self.tool_error: int = 0
        self.tool_counts_by_name: Counter = Counter()

        # 笔记
        self.cold_notes: list[str] = []

        # 规则
        self.rules_count: int = 0
        self.inactive_rules: list[str] = []

        # 日程/目标
        self.stale_goals_count: int = 0
        self.schedules_count: int = 0

        # 诊断
        self.diagnostics: list[str] = []

    def to_json(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "total_sessions": self.total_sessions,
            "total_messages": self.total_messages,
            "total_turns": self.total_turns,
            "memory": {
                "count": self.memory_count,
                "entries": self.memory_list[:20],
            },
            "lessons": {
                "count": self.lesson_count,
                "categories": dict(self.lesson_categories),
            },
            "tool_usage": {
                "total": self.tool_calls_total,
                "success": self.tool_success,
                "error": self.tool_error,
                "success_rate": (
                    round(self.tool_success / self.tool_calls_total, 4)
                    if self.tool_calls_total else None
                ),
                "by_name": dict(self.tool_counts_by_name.most_common()),
            },
            "cold_notes": self.cold_notes,
            "rules": {
                "count": self.rules_count,
                "inactive": self.inactive_rules,
            },
            "stale_goals": self.stale_goals_count,
            "schedules": self.schedules_count,
            "diagnostics": self.diagnostics,
        }


def _scan_sessions(config) -> tuple[Counter, int, int, int]:
    """扫描所有历史会话，返回 (工具调用计数器, 总调用, 成功, 失败)。

    返回: (name_counter, total, success, error)
    """
    name_counter: Counter = Counter()
    total = 0
    success = 0
    error = 0

    for sid, _mtime, _count in list_sessions(config):
        try:
            s = Session(config, session_id=sid)
        except Exception:
            continue
        for msg in s.messages:
            if msg.role != "assistant":
                continue
            for tc in msg.tool_calls or []:
                total += 1
                name_counter[tc.tool_name] += 1
                if tc.is_error:
                    error += 1
                else:
                    success += 1

    return name_counter, total, success, error


def audit(config, days: int = 90) -> MemoryAuditReport:
    """执行完整记忆审计。

    Args:
        days: 只统计最近 N 天的对话历史（默认 90 天），全量审计传 -1。
    """
    from ..skills.loader import scan_skills
    from ..rules.loader import scan_rules
    from ..goals import _load, GOAL_DIR_NAME
    from ..schedule import schedule_list as _schedule_list

    report = MemoryAuditReport()
    now = time.time()
    cutoff = now - days * 86400 if days >= 0 else 0

    # ① 会话与消息统计
    sessions = list_sessions(config)
    report.total_sessions = len(sessions)
    report.total_messages = sum(n for _sid, _mt, n in sessions)

    # 过滤到 days 范围内的会话，统计轮数
    recent_sessions = [(sid, mt, n) for sid, mt, n in sessions if mt >= cutoff]
    report.total_turns = sum(n for _sid, _mt, n in recent_sessions)

    # ② 长期记忆
    try:
        memories = longterm.list_all(config)
        report.memory_count = len(memories)
        report.memory_list = memories[:20]
    except Exception:
        report.memory_count = 0
        report.diagnostics.append("⚠ 长期记忆不可用（mem0 未就绪）")

    # ③ 教训
    try:
        lb = LessonsBlock(config)
        lessons = lb.list_lessons()
        report.lesson_count = len(lessons)
        for l in lessons:
            report.lesson_categories[l.get("category", "unknown")] += 1
    except Exception:
        pass

    # ④ 工具调用统计
    name_counter, total, success, error = _scan_sessions(config)
    report.tool_counts_by_name = name_counter
    report.tool_calls_total = total
    report.tool_success = success
    report.tool_error = error

    # ⑤ 规则统计
    try:
        rules = scan_rules(config)
        report.rules_count = len(rules)
        # 粗粒度：检查规则是否有被 agent 提及（从历史中搜 rule name）
        rule_names = {r.name for r in rules}
        for sid, _mt, _n in sessions[:50]:
            try:
                s = Session(config, session_id=sid)
                text = s.transcript()
                for rn in rule_names:
                    if rn in text:
                        report.inactive_rules.append(rn)  # actually active
            except Exception:
                continue
        # 反转：出现在 inactive 的是"曾被提及"，从 rules 中排除
        seen = set(report.inactive_rules)
        report.inactive_rules = [r for r in rule_names if r not in seen]
    except Exception:
        pass

    # ⑥ 冷笔记（近似：notes 表中存在但从未被 agent 引用）
    # 目前 notes 没有访问时间戳，用 transcript 匹配 source 做粗粒度估算
    try:
        from ..knowledge import store as knowledge_store
        from ..knowledge import embed as embed_mod

        db = knowledge_store.get_db(config)
        if "notes" in db.table_names():
            notes = db.open_table("notes").to_list()
            all_sources = {r.get("source") for r in notes if r.get("source")}
            referenced: set[str] = set()
            for sid, _mt, _n in sessions[:100]:
                try:
                    s = Session(config, session_id=sid)
                    t = s.transcript()
                    for src in all_sources:
                        if src in t:
                            referenced.add(src)
                except Exception:
                    continue
            report.cold_notes = sorted(all_sources - referenced)
    except Exception:
        pass

    # ⑦ 目标与日程
    try:
        goals_dir = config.path(config.paths.history_dir) / GOAL_DIR_NAME
        if goals_dir.exists():
            now = time.time()
            for f in goals_dir.glob("*.json"):
                data = json.loads(f.read_text(encoding="utf-8"))
                state = data.get("status", "")
                if state in ("active", "paused", "blocked"):
                    created = data.get("created_at", 0)
                    if (now - created) > 7 * 86400:
                        report.stale_goals_count += 1
    except Exception:
        pass

    try:
        sched = _schedule_list(config)
        report.schedules_count = sched.get("count", 0)
    except Exception:
        pass

    # ⑧ 生成诊断
    report.diagnostics.extend(_generate_diagnostics(report))

    return report


def _generate_diagnostics(r: MemoryAuditReport) -> list[str]:
    """基于审计结果生成诊断建议。"""
    msgs: list[str] = []

    # 记忆维度
    if r.memory_count == 0:
        msgs.append("🔴 长期记忆为空 —— 管家还不认识你，开始对话后自动沉淀。")
    elif r.memory_count < 5:
        msgs.append("🟡 长期记忆偏少 —— 管家对你的认知还比较浅。")
    else:
        msgs.append("🟢 长期记忆健康（{} 条）".format(r.memory_count))

    # 教训维度
    if r.lesson_count == 0:
        msgs.append("🟡 还没有 RefleXion 教训，对话中遇到可改进的场景后自动沉淀。")
    else:
        msgs.append("🟢 RefleXion 教训（{} 条，{} 个类别）".format(
            r.lesson_count, len(r.lesson_categories)
        ))

    # 工具调用维度
    if r.tool_calls_total > 0:
        rate = r.tool_success / r.tool_calls_total
        if rate < 0.8:
            msgs.append(
                "🔴 工具调用成功率 {:.1%}（{} 次失败）—— 建议检查常用工具配置".format(
                    rate, r.tool_error
                )
            )
        elif rate < 0.95:
            msgs.append(
                "🟡 工具调用成功率 {:.1%}（{} 次失败）—— 偶有失败，可观察趋势".format(
                    rate, r.tool_error
                )
            )
        else:
            msgs.append("🟢 工具调用成功率 {:.1%}（{} 次调用）".format(rate, r.tool_calls_total))

    # 冷笔记
    if r.cold_notes:
        msgs.append(
            "🟡 发现 {} 个冷笔记（索引后从未被 agent 引用）—— 可考虑清理或检查内容质量".format(
                len(r.cold_notes)
            )
        )

    # 死规则
    if r.inactive_rules:
        msgs.append(
            "🟡 发现 {} 条未使用规则：{} —— 可考虑删除或重新激活".format(
                len(r.inactive_rules),
                ", ".join(list(r.inactive_rules)[:5]) + ("..." if len(r.inactive_rules) > 5 else ""),
            )
        )

    # 过期目标
    if r.stale_goals_count > 0:
        msgs.append(
            "🟡 发现 {} 个过期目标（>7 天未更新）—— 建议清理 /goals 目录".format(
                r.stale_goals_count
            )
        )

    return msgs


def format_audit_human(report: MemoryAuditReport) -> str:
    """将审计结果格式化为富文本（用于 CLI 输出）。"""
    from rich.console import Console
    from rich.panel import Panel

    now_str = datetime.fromtimestamp(report.generated_at).strftime("%Y-%m-%d %H:%M")
    lines = [
        f"[bold]生成时间[/bold] {now_str}",
        f"[bold]总会话数[/bold] {report.total_sessions}",
        f"[bold]总消息数[/bold] {report.total_messages}",
        f"[bold]近 90 天轮次[/bold] {report.total_turns}",
        "",
        f"[bold]长期记忆[/bold] {report.memory_count} 条",
        f"[bold]教训[/bold] {report.lesson_count} 条（{len(report.lesson_categories)} 类）",
        "",
        f"[bold]工具调用[/bold] {report.tool_calls_total} 次",
    ]
    if report.tool_calls_total:
        rate = report.tool_success / report.tool_calls_total
        lines.append(f"  成功率 {rate:.1%}（失败 {report.tool_error}）")
        for name, cnt in report.tool_counts_by_name.most_common(10):
            lines.append(f"  {name}: {cnt}")

    lines.append(f"[bold]冷笔记[/bold] {len(report.cold_notes)} 个")
    lines.append(f"[bold]未使用规则[/bold] {len(report.inactive_rules)} 条")
    lines.append(f"[bold]过期目标[/bold] {report.stale_goals_count} 个")

    if report.diagnostics:
        lines.append("")
        lines.append("[bold]诊断建议[/bold]")
        for d in report.diagnostics:
            lines.append(f"  {d}")

    return "\n".join(lines)