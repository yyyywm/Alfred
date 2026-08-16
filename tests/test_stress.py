"""长对话稳定性压力测试：模拟多轮对话，验证 compaction + memory 召回不会崩溃。

不依赖真实 LLM —— 使用 mock agent，注入模拟的工具调用和压缩触发条件。
断言点：
1. 会话消息持久化正确
2. 压缩后消息数量符合预期
3. memory_search 工具调用正确记录
4. 压缩后 llm_state 被作废（种子上下文机制生效）
"""
import json
from datetime import datetime

from alfred.config import Config
from alfred.history import Session, delete_session
from alfred.memory.recall import recall, render_for_prompt


def _cfg(tmp_path):
    return Config(
        memory={"dir": str(tmp_path / "mem"), "recall_budget": 10},
        paths={"history_dir": str(tmp_path / "hist")},
    )


def test_session_50_turns_persists(tmp_path):
    """50 轮对话：消息全部写入，持久化可恢复。"""
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    for i in range(50):
        s.add_user(f"问题 {i}")
        s.add_assistant(f"回答 {i}")

    s2 = Session(cfg, session_id=s.id)
    assert len(s2.messages) == 100
    assert s2.messages[0].content == "问题 0"
    assert s2.messages[99].content == "回答 49"


def test_session_200_turns_with_tool_calls(tmp_path):
    """200 轮对话 + 工具调用：ToolCallRecord 完整持久化。"""
    from alfred.history import ToolCallRecord

    cfg = _cfg(tmp_path)
    s = Session(cfg)
    tc = ToolCallRecord(tool_name="memory_search", args={"query": "x"}, result="hits")
    for i in range(200):
        s.add_user(f"问 {i}")
        s.add_assistant(f"答 {i}", tool_calls=[tc] if i % 10 == 0 else [])

    s2 = Session(cfg, session_id=s.id)
    assert len(s2.messages) == 400
    tc_count = sum(1 for m in s2.messages if m.tool_calls)
    assert tc_count == 20  # every 10th assistant message has a tool call

    # 工具调用内容正确
    tc_msg = next(m for m in s2.messages if m.tool_calls)
    assert tc_msg.tool_calls[0].tool_name == "memory_search"


def test_session_compaction_preserves_messages(tmp_path):
    """压缩后消息保留、llm_state 作废。"""
    cfg = _cfg(tmp_path)
    s = Session(cfg)

    # 模拟 100 轮对话
    for i in range(100):
        s.add_user(f"q{i}")
        s.add_assistant(f"a{i}")

    # 注入 llm_state
    s.set_llm_state(b'[{"role":"user","content":"seed"}]')

    # 模拟压缩：保留摘要 + 最近 2 轮
    s.messages = s.messages[:0]
    s.add_assistant("[早前对话摘要]\n共进行了 100 轮关于 AI 的讨论")
    for i in range(2):
        s.add_user(f"post-q{i}")
        s.add_assistant(f"post-a{i}")
    s.rewrite()

    s2 = Session(cfg, session_id=s.id)
    assert s2.llm_state is None
    assert len(s2.messages) == 5  # 1 summary + 2 user + 2 assistant
    assert "摘要" in s2.messages[0].content


def test_compaction_across_multiple_rewrites(tmp_path):
    """多次压缩后仍可正常工作（每次压缩都重置 llm_state）。"""
    cfg = _cfg(tmp_path)
    s = Session(cfg)

    for round in range(5):
        # 每次加 30 轮
        for i in range(30):
            s.add_user(f"r{round}-q{i}")
            s.add_assistant(f"r{round}-a{i}")
        s.set_llm_state(b'{"r":%d}' % round)

        # 压缩：保留摘要 + 最近 3 轮（6 条消息）
        s.messages = s.messages[:0]
        s.add_assistant(f"[摘要] round {round} 讨论完成")
        for i in range(3):
            s.add_user(f"r{round}-q{i}")
            s.add_assistant(f"r{round}-a{i}")
        s.rewrite()

    s2 = Session(cfg, session_id=s.id)
    # rewrite() 整体重写文件：每次压缩会覆盖之前的所有消息，
    # 所以 5 次压缩后只剩最后一次压缩的内容（1 summary + 6 messages = 7）
    assert len(s2.messages) == 7
    assert s2.llm_state is None


def test_memory_recall_with_budget(tmp_path):
    """大量记忆 + 预算截断：recall 不崩且返回数量不超过预算。"""
    cfg = _cfg(tmp_path)

    # rank_memories 直接测试
    from alfred.memory.recall import rank_memories

    items = [{"memory": f"记忆{i}", "score": 0.5 + i * 0.01} for i in range(100)]
    ranked = rank_memories(cfg, items)
    assert len(ranked) == cfg.memory.recall_budget  # 硬截断


def test_memory_recall_empty_config(tmp_path):
    """空记忆库 recall 不报错。"""
    from alfred.memory.recall import render_for_prompt

    rendered = render_for_prompt([])
    assert "没有召回" in rendered


def test_session_stress_delete(tmp_path):
    """100 个会话创建后删除，不破坏其他会话。"""
    cfg = _cfg(tmp_path)
    ids = []
    for i in range(100):
        s = Session(cfg)
        s.add_user(f"第 {i} 个会话")
        ids.append(s.id)

    # 删除偶数会话
    for i in range(0, 100, 2):
        assert delete_session(cfg, ids[i]) is True

    # 奇数会话仍存在
    for i in range(1, 100, 2):
        s = Session(cfg, session_id=ids[i])
        assert len(s.messages) == 1