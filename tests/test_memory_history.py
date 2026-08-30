"""召回排序与预算测试、会话历史测试。"""

import time
from datetime import datetime

from alfred.config import Config
from alfred.history import Session, delete_session, list_sessions
from alfred.memory.recall import rank_memories, render_for_prompt


def _cfg(tmp_path, budget=3):
    return Config(
        memory={"dir": str(tmp_path / "mem"), "recall_budget": budget},
        paths={"history_dir": str(tmp_path / "hist")},
    )


def _iso(ts: float) -> str:
    """mem0 风格的无时区 ISO 时间戳。"""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")


def test_recall_budget(tmp_path):
    cfg = _cfg(tmp_path, budget=3)
    items = [{"memory": f"记忆{i}", "score": 0.5 + i * 0.05} for i in range(10)]
    ranked = rank_memories(cfg, items)
    assert len(ranked) == 3  # 硬预算截断


def test_parse_ts_formats():
    """mem0 的 created_at 是无时区 ISO，必须能被解析——否则 recency 恒为 0.5。"""
    from alfred.memory.recall import _parse_ts

    assert _parse_ts("2026-08-30T15:16:00") is not None        # mem0 真实格式（无时区）
    assert _parse_ts("2026-08-30T15:16:00.123456") is not None
    assert _parse_ts("2026-08-30 15:16:00") is not None
    assert _parse_ts("2026-08-30T15:16:00+08:00") is not None
    assert _parse_ts(1700000000) == 1700000000.0
    assert _parse_ts(None) is None
    assert _parse_ts("not-a-date") is None


def test_recency_boost(tmp_path):
    """近因度能真正翻转排序：新鲜的中等相关度应胜过陈旧的极高相关度。

    回归：_parse_ts 只试带 %z 的格式，mem0 无时区时间戳全部解析失败，
    recency 恒为 0.5，0.3 的近因权重形同虚设。
    """
    cfg = _cfg(tmp_path)
    now = time.time()
    old_relevant = {"memory": "旧但相关", "score": 0.9,
                    "created_at": _iso(now - 60 * 86400)}  # 2 个半衰期 → recency 0.25
    new_less_relevant = {"memory": "新但略逊", "score": 0.7,
                         "created_at": _iso(now)}          # recency 1.0
    ranked = rank_memories(cfg, [old_relevant, new_less_relevant])
    assert ranked[0]["memory"] == "新但略逊"
    # 同等相关度时，近因度是唯一区分因素（把陈旧项放前面，确认不是排序稳定性假象）
    same_rel = [
        {"memory": "陈旧同分", "score": 0.7, "created_at": _iso(now - 60 * 86400)},
        {"memory": "新鲜同分", "score": 0.7, "created_at": _iso(now)},
    ]
    assert rank_memories(cfg, same_rel)[0]["memory"] == "新鲜同分"


def test_zero_score_not_remapped(tmp_path):
    """score 为 0.0 是合法低分，不能被 `or` 重映射成默认 0.5。"""
    cfg = _cfg(tmp_path)
    now = time.time()
    ranked = rank_memories(
        cfg,
        [
            {"memory": "零分但新鲜", "score": 0.0, "created_at": _iso(now)},
            {"memory": "零分且陈旧", "score": 0.0, "created_at": _iso(now - 60 * 86400)},
            {"memory": "中等相关无时间", "score": 0.5},
        ],
    )
    # 0.0 分 + 新鲜 recency=1.0 → 0.30；中等相关 0.5 → 0.50；陈旧 0.0 → 0.075
    assert ranked[0]["memory"] == "中等相关无时间"
    assert ranked[-1]["memory"] == "零分且陈旧"


def test_render_empty():
    assert "没有召回" in render_for_prompt([])


def test_session_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("你好")
    s.add_assistant("你好，有什么可以帮你？")
    s.add_tool("notes_search", "结果", tool_call_id="t1")
    s.set_llm_state(b'[{"role":"user"}]')

    s2 = Session(cfg, session_id=s.id)
    assert len(s2.messages) == 3
    assert s2.messages[0].content == "你好"
    assert s2.llm_state == b'[{"role":"user"}]'


def test_session_rewrite_clears_llm_state(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("旧消息")
    s.set_llm_state(b"state")
    s.messages = s.messages[:0]
    s.add_assistant("[早前对话摘要]\n摘要内容")
    s.rewrite()

    s2 = Session(cfg, session_id=s.id)
    assert s2.llm_state is None
    assert len(s2.messages) == 1
    assert "摘要" in s2.messages[0].content


def test_transcript(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("问题")
    s.add_assistant("回答")
    t = s.transcript()
    assert "[user] 问题" in t and "[assistant] 回答" in t


def test_delete_session(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("要删除的会话")
    assert s.file.exists()
    assert delete_session(cfg, s.id) is True
    assert not s.file.exists()
    # 删除不存在的会话返回 False；路径穿越会被收敛为文件名
    assert delete_session(cfg, s.id) is False
    assert delete_session(cfg, "../outside") is False


def test_list_sessions_excludes_meta_files(tmp_path):
    """consolidate 元数据/待审草稿文件不应被当成会话历史列出。

    回归：consolidate_pending.jsonl 的 drafts 字段曾导致
    Session._load → Message(**record) 抛 TypeError。
    """
    cfg = _cfg(tmp_path)
    # 正常会话
    s = Session(cfg)
    s.add_user("会话内容")
    # meta 文件（模拟 consolidate 产物）
    for name in ("consolidate_state.jsonl", "consolidate_pending.jsonl"):
        (cfg.path(cfg.paths.history_dir) / name).write_text(
            '{"drafts": {"x": 1}, "ts": 1}\n', encoding="utf-8"
        )

    sessions = list_sessions(cfg)
    ids = {sid for sid, _m, _n in sessions}
    assert ids == {s.id}
    # 且 meta 文件不会导致下游解析崩溃
    for sid, _m, _n in sessions:
        sess = Session(cfg, session_id=sid)
        assert len(sess.messages) == 1
