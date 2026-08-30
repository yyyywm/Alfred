"""审计报告的 days 时间窗一致性测试（不触碰真实 LLM/向量库）。

回归：audit(days=N) 之前只把 total_turns 过滤到时间窗内，工具调用统计
（④）仍扫全部历史会话——同一份报告里"轮数"是近 N 天、"工具成功率"却是
终身统计，成功率会随历史增长而漂移。
"""

import time
from types import SimpleNamespace

from alfred.config import Config
from alfred.memory import audit as audit_mod


def _assistant(*tool_calls):
    """构造一条 assistant 消息：[(tool_name, is_error), ...]。"""
    return SimpleNamespace(
        role="assistant",
        tool_calls=[SimpleNamespace(tool_name=n, is_error=e) for n, e in tool_calls],
    )


# 两个会话：recent 1 天前、stale 30 天前。tool_calls_total 差值即窗口是否生效。
# 用真实时钟相对偏移，而不是固定时间戳——audit() 内部自己取 time.time() 当 now，
# 固定戳会让测试跑在 2001 年之后时全部出窗。
_NOW = time.time()
_ROWS = [
    ("recent", _NOW - 86400, 1),
    ("stale", _NOW - 30 * 86400, 1),
]
_MESSAGES = {
    "recent": [_assistant(("file_read", False), ("file_read", True))],
    "stale": [_assistant(*[("shell_exec", False)] * 5)],
}


def _make_fake_session():
    class FakeSession:
        """只暴露 _scan_sessions 用到的 messages 属性。"""

        def __init__(self, config, session_id="x"):
            self.messages = _MESSAGES[session_id]

    return FakeSession


def _install(monkeypatch):
    """统一打桩：会话列表、Session 解析、mem0、lessons git 仓库。"""
    monkeypatch.setattr(audit_mod, "list_sessions", lambda cfg: _ROWS)
    monkeypatch.setattr(audit_mod, "Session", _make_fake_session())
    monkeypatch.setattr(audit_mod.longterm, "list_all", lambda cfg: [])
    monkeypatch.setattr(
        audit_mod, "LessonsBlock",
        lambda cfg: SimpleNamespace(list_lessons=lambda: []),
    )
    return Config(paths={"history_dir": "hist", "vectordb_dir": "vdb"})


def test_scan_sessions_filters_by_cutoff(monkeypatch):
    """cutoff 只保留 mtime >= cutoff 的会话；cutoff=0 表示全量。"""
    cfg = _install(monkeypatch)

    counter, total, success, error = audit_mod._scan_sessions(cfg, cutoff=_NOW - 2 * 86400)
    assert (total, success, error) == (2, 1, 1)
    assert dict(counter) == {"file_read": 2}  # stale 的 shell_exec 被排除

    # cutoff=0 → 全量（audit(days=-1) 的语义）
    counter, total, success, error = audit_mod._scan_sessions(cfg, cutoff=0.0)
    assert total == 7 and counter["shell_exec"] == 5


def test_audit_forwards_days_window_to_tool_stats(monkeypatch):
    """audit(days=N) 必须把时间窗传给工具统计，而不是只在 total_turns 上生效。"""
    cfg = _install(monkeypatch)

    report = audit_mod.audit(cfg, days=7)
    assert report.total_turns == 1           # stale 会话已出窗
    assert report.tool_calls_total == 2      # 与 total_turns 同窗，不是终身统计
    assert "shell_exec" not in report.tool_counts_by_name

    # days=-1 → 全量审计，stale 会话重新计入
    full = audit_mod.audit(cfg, days=-1)
    assert full.total_turns == 2
    assert full.tool_calls_total == 7
