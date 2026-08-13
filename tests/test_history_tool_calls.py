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


def test_multiple_tool_calls_on_one_assistant_message(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)

    calls = [
        ToolCallRecord(tool_name="a", args={"x": 1}, result="r1", tool_call_id="tc-1"),
        ToolCallRecord(tool_name="b", args={"y": 2}, result="r2", tool_call_id="tc-2"),
    ]
    s.add_assistant("calling", tool_calls=calls)

    s2 = Session(cfg, session_id=s.id)
    msg = s2.messages[0]
    assert len(msg.tool_calls) == 2
    assert [tc.tool_name for tc in msg.tool_calls] == ["a", "b"]
    assert msg.tool_calls[0].tool_call_id == "tc-1"
    assert msg.tool_calls[1].tool_call_id == "tc-2"


def test_tool_call_record_is_error_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)

    record = ToolCallRecord(
        tool_name="shell",
        args={"cmd": "exit 1"},
        result="error message",
        is_error=True,
        tool_call_id="tc-err",
    )
    s.add_assistant("failed.", tool_calls=[record])

    s2 = Session(cfg, session_id=s.id)
    restored = s2.messages[0].tool_calls[0]
    assert restored.is_error is True
    assert restored.tool_call_id == "tc-err"


def test_assistant_message_with_empty_tool_calls(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_assistant("plain reply", tool_calls=[])

    s2 = Session(cfg, session_id=s.id)
    assert len(s2.messages) == 1
    assert s2.messages[0].tool_calls == []


def test_rewrite_preserves_tool_calls(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_assistant(
        "result",
        tool_calls=[
            ToolCallRecord(
                tool_name="run_python",
                args={"code": "1+1"},
                result="2",
                tool_call_id="tc-py",
            )
        ],
    )
    s.rewrite()

    s2 = Session(cfg, session_id=s.id)
    msg = s2.messages[0]
    assert msg.role == "assistant"
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].tool_call_id == "tc-py"


def test_backward_compat_load_record_without_tool_calls_key(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_user("hello")
    s.rewrite()

    # Simulate an old JSONL line written before tool_calls existed.
    old_record = {
        "role": "user",
        "content": "legacy",
        "ts": 1.0,
        "name": None,
        "tool_call_id": None,
        "compacted": False,
    }
    s.file.write_text(
        __import__("json").dumps(old_record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    s2 = Session(cfg, session_id=s.id)
    assert len(s2.messages) == 1
    msg = s2.messages[0]
    assert msg.role == "user"
    assert msg.content == "legacy"
    assert msg.tool_calls == []


def test_add_tool_roundtrip_with_tool_call_id(tmp_path):
    cfg = _cfg(tmp_path)
    s = Session(cfg)
    s.add_tool(name="notes_search", content="result text", tool_call_id="tc-123")

    s2 = Session(cfg, session_id=s.id)
    assert len(s2.messages) == 1
    msg = s2.messages[0]
    assert msg.role == "tool"
    assert msg.name == "notes_search"
    assert msg.content == "result text"
    assert msg.tool_call_id == "tc-123"
