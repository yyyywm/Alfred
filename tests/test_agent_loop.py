# tests/test_agent_loop.py
import pytest
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelResponse, TextPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from alfred.agent import AlfredDeps, _wrap_tool, chat_turn_stream
from alfred.config import Config, ProviderConfig
from alfred.events import (
    AssistantChunk,
    EventBus,
    ToolCallEnd,
    ToolCallStart,
    ToolDenied,
    TurnEnd,
    TurnError,
    TurnStart,
)
from alfred.history import Session


def _test_config(tmp_path):
    return Config(
        providers={
            "dummy": ProviderConfig(type="openai_compat", models=["m"]),
        },
        paths={"history_dir": str(tmp_path / "hist")},
    )


def _make_tool_stream():
    """Return a stream_function that yields text, one tool call, then a final text."""
    async def stream(messages, info):
        has_tool_return = any(
            getattr(m, "parts", None) and any(
                isinstance(p, ToolReturnPart) for p in m.parts
            )
            for m in messages
        )
        if has_tool_return:
            yield "done"
            return
        yield "Thinking..."
        yield {0: DeltaToolCall(name="greet", json_args='{"name":"Ada"}', tool_call_id="tc1")}

    return stream


def test_chat_turn_stream_emits_events(tmp_path):
    cfg = _test_config(tmp_path)
    agent = Agent(FunctionModel(stream_function=_make_tool_stream()), deps_type=AlfredDeps)

    def greet(ctx: RunContext[AlfredDeps], name: str) -> str:
        return f"Hello {name}"

    agent.tool(_wrap_tool(greet, "greet"))

    session = Session(cfg)
    deps = AlfredDeps(config=cfg, blocks=None, confirm=lambda _msg: True)
    bus = EventBus()
    collected = []
    bus.subscribe(collected.append)

    events = list(chat_turn_stream(agent, deps, session, "say hi", bus=bus))

    assert collected == events
    assert any(isinstance(e, TurnStart) for e in events)
    assert [e.delta for e in events if isinstance(e, AssistantChunk)] == [
        "Thinking...",
        "done",
    ]
    tool_starts = [e for e in events if isinstance(e, ToolCallStart)]
    assert len(tool_starts) == 1
    assert tool_starts[0].tool_name == "greet"
    tool_ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(tool_ends) == 1
    assert tool_ends[0].result == "Hello Ada"
    assert tool_ends[0].args == {"name": "Ada"}
    assert any(isinstance(e, TurnEnd) for e in events)

    assert len(session.messages) == 2
    assert session.messages[1].role == "assistant"
    assert session.messages[1].tool_calls[0].tool_name == "greet"
    assert session.messages[1].tool_calls[0].tool_call_id == "tc1"


def _make_text_then_tool_stream():
    """Stream text before a tool call, then final text after the tool returns."""
    async def stream(messages, info):
        has_tool_return = any(
            getattr(m, "parts", None) and any(
                isinstance(p, ToolReturnPart) for p in m.parts
            )
            for m in messages
        )
        if has_tool_return:
            yield "Done."
            return
        yield "Let me greet."
        yield {0: DeltaToolCall(name="greet", json_args='{"name":"Ada"}', tool_call_id="tc1")}

    return stream


def test_chat_turn_stream_preserves_text_across_tool_loop(tmp_path):
    """Regression: assistant text before and after a tool call is persisted."""
    cfg = _test_config(tmp_path)
    agent = Agent(FunctionModel(stream_function=_make_text_then_tool_stream()), deps_type=AlfredDeps)

    def greet(ctx: RunContext[AlfredDeps], name: str) -> str:
        return f"Hello {name}"

    agent.tool(_wrap_tool(greet, "greet"))

    session = Session(cfg)
    deps = AlfredDeps(config=cfg, blocks=None, confirm=lambda _msg: True)
    events = list(chat_turn_stream(agent, deps, session, "say hi"))

    chunks = [e.delta for e in events if isinstance(e, AssistantChunk)]
    assert "Let me greet." in chunks
    assert "Done." in chunks

    assert len(session.messages) == 2
    assistant_msg = session.messages[1]
    assert assistant_msg.role == "assistant"
    assert "Let me greet." in assistant_msg.content
    assert "Done." in assistant_msg.content
    assert assistant_msg.tool_calls[0].tool_name == "greet"


def _make_broken_stream():
    """A stream function that yields once then raises."""
    async def stream(messages, info):
        yield "starting"
        raise RuntimeError("model exploded")

    return stream


def test_chat_turn_stream_emits_turn_error_on_failure(tmp_path):
    """When the agent run raises, a TurnError event is emitted before the stream ends."""
    cfg = _test_config(tmp_path)
    agent = Agent(FunctionModel(stream_function=_make_broken_stream()), deps_type=AlfredDeps)

    session = Session(cfg)
    deps = AlfredDeps(config=cfg, blocks=None)
    bus = EventBus()
    events = []
    bus.subscribe(events.append)

    with pytest.raises(Exception):
        list(chat_turn_stream(agent, deps, session, "hi", bus=bus))

    turn_errors = [e for e in events if isinstance(e, TurnError)]
    assert len(turn_errors) == 1
    assert turn_errors[0].session_id == session.id
    assert "model exploded" in turn_errors[0].error


def _make_denied_tool_stream():
    """Return a stream that calls a confirmation-required tool, then final text."""
    async def stream(messages, info):
        has_tool_return = any(
            getattr(m, "parts", None) and any(
                isinstance(p, ToolReturnPart) for p in m.parts
            )
            for m in messages
        )
        if has_tool_return:
            yield "done"
            return
        yield {0: DeltaToolCall(name="shell", json_args='{"command":"echo hi"}', tool_call_id="tc1")}

    return stream


def test_chat_turn_stream_emits_tool_denied(tmp_path):
    """When the user denies a tool call, emit ToolDenied and record the error."""
    cfg = _test_config(tmp_path)
    agent = Agent(FunctionModel(stream_function=_make_denied_tool_stream()), deps_type=AlfredDeps)

    calls = []

    def shell(ctx: RunContext[AlfredDeps], command: str) -> str:
        calls.append(command)
        return f"ran {command}"

    agent.tool(_wrap_tool(shell, "shell"))

    session = Session(cfg)
    deps = AlfredDeps(config=cfg, blocks=None, confirm=lambda _msg: False)
    bus = EventBus()
    events = []
    bus.subscribe(events.append)

    result_events = list(chat_turn_stream(agent, deps, session, "run command", bus=bus))
    assert result_events == events

    denied = [e for e in events if isinstance(e, ToolDenied)]
    assert len(denied) == 1
    assert denied[0].tool_name == "shell"
    assert denied[0].args == {"command": "echo hi"}
    assert denied[0].tool_call_id == "tc1"

    tool_ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(tool_ends) == 1
    assert tool_ends[0].is_error is True
    assert tool_ends[0].tool_name == "shell"
    assert tool_ends[0].tool_call_id == "tc1"

    assert calls == []

    assert any(isinstance(e, TurnEnd) for e in events)

    assert len(session.messages) == 2
    assistant_msg = session.messages[1]
    assert assistant_msg.role == "assistant"
    assert assistant_msg.tool_calls[0].tool_name == "shell"
    assert assistant_msg.tool_calls[0].is_error is True
    assert "done" in assistant_msg.content
