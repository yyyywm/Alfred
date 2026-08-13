from alfred.events import (
    AssistantChunk,
    ContextCompacted,
    EventBus,
    ToolCallEnd,
    ToolCallStart,
    ToolDenied,
    TurnEnd,
    TurnStart,
)


def test_subscribe_and_emit():
    received = []
    bus = EventBus()
    bus.subscribe(received.append)

    event = TurnStart(session_id="s1", user_text="hello")
    bus.emit(event)

    assert received == [event]


def test_multiple_listeners():
    a, b = [], []
    bus = EventBus()
    bus.subscribe(a.append)
    bus.subscribe(b.append)

    event = AssistantChunk(session_id="s1", delta="hi")
    bus.emit(event)

    assert a == [event]
    assert b == [event]


def test_listener_error_does_not_break_others():
    received = []
    bus = EventBus()
    bus.subscribe(lambda _e: 1 / 0)
    bus.subscribe(received.append)

    event = TurnStart(session_id="s1", user_text="hello")
    bus.emit(event)

    assert received == [event]


def test_all_event_types_round_trip():
    """构造并发射全部事件类型，校验 payload 原样到达 listener。"""
    received = []
    bus = EventBus()
    bus.subscribe(received.append)

    events = [
        TurnStart(session_id="s1", user_text="hello"),
        AssistantChunk(session_id="s1", delta="world"),
        ToolCallStart(session_id="s1", tool_name="shell", args={"command": "ls"}),
        ToolCallEnd(
            session_id="s1",
            tool_name="shell",
            args={"command": "ls"},
            result="file.txt",
            is_error=False,
        ),
        ToolDenied(session_id="s1", tool_name="shell", args={"command": "rm -rf /"}, reason="dangerous"),
        TurnEnd(session_id="s1", usage={"prompt_tokens": 10, "completion_tokens": 5}),
        ContextCompacted(session_id="s1", summary="summary", retained_message_count=3),
    ]

    for event in events:
        bus.emit(event)

    assert received == events
    # 额外校验默认值与可选字段确实被保留。
    assert received[3].is_error is False
    assert received[5].usage == {"prompt_tokens": 10, "completion_tokens": 5}
    assert received[6].retained_message_count == 3
