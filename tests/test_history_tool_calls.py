from alfred.config import Config
from alfred.history import Message, Session, ToolCallRecord


def _cfg(tmp_path):
    return Config(paths={"history_dir": str(tmp_path / "hist")})


def test_assistant_message_with_tool_calls_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)

    record = ToolCallRecord(
        tool_name="notes_search",
        args={"query": "ok", "limit": 3},
        result="one result",
        is_error=False,
    )
    s.add_assistant("I found this.", tool_calls=[record])

    s2 = Session(cfg, session_id=s.id)
    assert len(s2.messages) == 1
    msg = s2.messages[0]
    assert msg.role == "assistant"
    assert msg.content == "I found this."
    assert len(msg.tool_calls) == 1
    restored = msg.tool_calls[0]
    assert restored.tool_name == "notes_search"
    assert restored.args == {"query": "ok", "limit": 3}
    assert restored.result == "one result"
    assert restored.is_error is False
