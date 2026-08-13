# tests/test_agent_loop.py
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from alfred.agent import AlfredDeps, _wrap_tool, chat_turn_stream
from alfred.config import Config, ProviderConfig
from alfred.events import (
    AssistantChunk,
    EventBus,
    ToolCallEnd,
    ToolCallStart,
    TurnEnd,
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
                type(p).__name__ == "ToolReturnPart" for p in m.parts
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
