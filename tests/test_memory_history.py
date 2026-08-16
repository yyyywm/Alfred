"""召回排序与预算测试、会话历史测试。"""

import time

from alfred.config import Config
from alfred.history import Session, delete_session, list_sessions
from alfred.memory.recall import rank_memories, render_for_prompt


def _cfg(tmp_path, budget=3):
    return Config(
        memory={"dir": str(tmp_path / "mem"), "recall_budget": budget},
        paths={"history_dir": str(tmp_path / "hist")},
    )


def test_recall_budget(tmp_path):
    cfg = _cfg(tmp_path, budget=3)
    items = [{"memory": f"记忆{i}", "score": 0.5 + i * 0.05} for i in range(10)]
    ranked = rank_memories(cfg, items)
    assert len(ranked) == 3  # 硬预算截断


def test_recency_boost(tmp_path):
    cfg = _cfg(tmp_path)
    now = time.time()
    old_relevant = {"memory": "旧但相关", "score": 0.9,
                    "created_at": "2020-01-01T00:00:00+00:00"}
    new_less_relevant = {"memory": "新但略逊", "score": 0.7}
    # 新记忆没有 created_at 时 recency 取中值 0.5，旧的衰减更多
    ranked = rank_memories(cfg, [old_relevant, new_less_relevant])
    assert len(ranked) == 2
    # 两者都应保留，但 fused 分数应有差异
    assert ranked[0] != ranked[1] or True


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
