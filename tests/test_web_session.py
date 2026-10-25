"""WebSession 测试：事件序列化、确认桥接。"""

import asyncio
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alfred.config import Config
from alfred.events import (
    AssistantChunk,
    ContextCompacted,
    EventBus,
    ToolCallEnd,
    ToolCallStart,
    ToolDenied,
    TurnEnd,
    TurnError,
    TurnRetrying,
    TurnStart,
)


# ── 辅助函数 ──────────────────────────────────────────────────────

def _make_config(tmp_path) -> Config:
    """创建最小化测试配置。"""
    return Config(
        providers={
            "test": {
                "type": "openai_compat",
                "base_url": "https://api.example.com",
                "env_key": "",
                "models": ["test-model"],
            }
        },
        models={"chat": "test:test-model", "memory_write": "test:test-model"},
        memory={"dir": str(tmp_path / "memory")},
        paths={"history_dir": str(tmp_path / "history")},
    )


# ── 事件序列化测试（纯静态方法，不需要 mock） ────────────────────

class TestEventSerialization:
    """测试事件序列化（静态方法，直接测试）。"""

    def test_turn_start(self):
        from alfred.server.session import WebSession
        event = TurnStart(session_id="s1", user_text="hello")
        result = WebSession._serialize_event(event)
        assert result == {"type": "turn_start", "session_id": "s1"}

    def test_assistant_chunk(self):
        from alfred.server.session import WebSession
        event = AssistantChunk(session_id="s1", delta="hi there")
        result = WebSession._serialize_event(event)
        assert result == {"type": "assistant_chunk", "delta": "hi there"}

    def test_tool_call_start(self):
        from alfred.server.session import WebSession
        event = ToolCallStart(session_id="s1", tool_name="shell", args={"command": "ls"})
        result = WebSession._serialize_event(event)
        assert result["type"] == "tool_call_start"
        assert result["tool_name"] == "shell"
        assert result["args"] == {"command": "ls"}

    def test_tool_call_end(self):
        from alfred.server.session import WebSession
        event = ToolCallEnd(session_id="s1", tool_name="shell", args={"command": "ls"}, result="file.txt", is_error=False)
        result = WebSession._serialize_event(event)
        assert result["type"] == "tool_call_end"
        assert result["is_error"] is False
        assert result["result"] == "file.txt"

    def test_tool_denied(self):
        from alfred.server.session import WebSession
        event = ToolDenied(session_id="s1", tool_name="shell", args={}, reason="denied")
        result = WebSession._serialize_event(event)
        assert result["type"] == "tool_denied"
        assert result["reason"] == "denied"

    def test_turn_end(self):
        from alfred.server.session import WebSession
        event = TurnEnd(session_id="s1", usage={"prompt_tokens": 10})
        result = WebSession._serialize_event(event)
        assert result["type"] == "turn_end"
        assert result["usage"] == {"prompt_tokens": 10}

    def test_turn_error(self):
        from alfred.server.session import WebSession
        event = TurnError(session_id="s1", error="something went wrong")
        result = WebSession._serialize_event(event)
        assert result["type"] == "turn_error"
        assert result["error"] == "something went wrong"

    def test_turn_retrying(self):
        from alfred.server.session import WebSession
        event = TurnRetrying(session_id="s1", attempt=1, max_retries=3, wait_seconds=2.0, error="timeout")
        result = WebSession._serialize_event(event)
        assert result["type"] == "turn_retrying"
        assert result["attempt"] == 1
        assert result["wait_seconds"] == 2.0

    def test_context_compacted(self):
        from alfred.server.session import WebSession
        event = ContextCompacted(session_id="s1", summary="summary", retained_message_count=5)
        result = WebSession._serialize_event(event)
        assert result["type"] == "context_compacted"
        assert result["summary"] == "summary"
        assert result["retained_message_count"] == 5

    def test_unknown_event(self):
        from alfred.server.session import WebSession
        class UnknownEvent:
            pass
        result = WebSession._serialize_event(UnknownEvent())
        assert result["type"] == "unknown_event"

    def test_truncate_long_result(self):
        """ToolCallEnd 结果应被截断到 3000 字符。"""
        from alfred.server.session import WebSession
        long_result = "x" * 5000
        event = ToolCallEnd(session_id="s1", tool_name="t", args={}, result=long_result, is_error=False)
        result = WebSession._serialize_event(event)
        assert len(result["result"]) == 3000


# ── 确认桥接测试（直接测试真实的 ConfirmBridge，无 asyncio 依赖） ──

class TestConfirmBridge:
    """测试 ConfirmBridge（线程安全、approved 值不丢失、close 自动拒绝）。

    ConfirmBridge 不依赖 asyncio 事件循环，纯 threading 测试，Windows 可运行。
    """

    def _make_bridge(self, timeout_s=5):
        from alfred.server.session import ConfirmBridge
        return ConfirmBridge(timeout_s=timeout_s)

    def _run_confirm(self, bridge, result):
        def _callback():
            result["confirmed"] = bridge.confirm("允许吗？", tool_name="shell")
        thread = threading.Thread(target=_callback, daemon=True)
        thread.start()
        return thread

    def _pending_id(self, bridge):
        with bridge._lock:
            assert bridge._pending is not None
            return bridge._pending["id"]

    def test_confirm_approved(self):
        """批准路径：respond(True) → confirm 返回 True。"""
        bridge = self._make_bridge()
        result = {}
        thread = self._run_confirm(bridge, result)
        time.sleep(0.1)

        confirm_id = self._pending_id(bridge)
        assert bridge.respond(confirm_id, True) is True

        thread.join(timeout=5)
        assert not thread.is_alive(), "confirm 线程应已结束"
        assert result["confirmed"] is True

    def test_confirm_denied(self):
        """拒绝路径：respond(False) → confirm 必须返回 False（回归：approved 不得丢失）。"""
        bridge = self._make_bridge()
        result = {"confirmed": True}
        thread = self._run_confirm(bridge, result)
        time.sleep(0.1)

        confirm_id = self._pending_id(bridge)
        assert bridge.respond(confirm_id, False) is True

        thread.join(timeout=5)
        assert not thread.is_alive()
        assert result["confirmed"] is False, "用户拒绝后 confirm 必须返回 False"

    def test_respond_to_nonexistent_confirm(self):
        """对不存在的确认请求响应应返回 False。"""
        bridge = self._make_bridge()
        assert bridge.respond("nonexistent", True) is False

    def test_respond_with_wrong_id(self):
        """id 不匹配的响应应返回 False，且不解除挂起。"""
        bridge = self._make_bridge()
        result = {}
        thread = self._run_confirm(bridge, result)
        time.sleep(0.1)

        assert bridge.respond("wrong-id", True) is False
        # 用正确 id 响应以清理线程
        confirm_id = self._pending_id(bridge)
        bridge.respond(confirm_id, True)
        thread.join(timeout=5)
        assert result["confirmed"] is True

    def test_confirm_timeout_returns_false(self):
        """确认请求超时后应返回 False。"""
        bridge = self._make_bridge(timeout_s=0.2)
        start = time.monotonic()
        result = bridge.confirm("超时测试", tool_name="shell")
        elapsed = time.monotonic() - start
        assert result is False
        assert elapsed < 2, "应在超时后返回而非挂起"

    def test_close_auto_denies_pending(self):
        """close() 后挂起的确认应立即解除阻塞并返回 False。"""
        bridge = self._make_bridge(timeout_s=60)
        result = {"confirmed": True}
        thread = self._run_confirm(bridge, result)
        time.sleep(0.1)

        bridge.close()

        thread.join(timeout=5)
        assert not thread.is_alive(), "close 后 confirm 线程应立即结束"
        assert result["confirmed"] is False

    def test_confirm_after_close_returns_false(self):
        """close() 后再调用 confirm 直接返回 False，不阻塞。"""
        bridge = self._make_bridge()
        bridge.close()
        assert bridge.confirm("还来吗？") is False

    def test_on_request_callback_receives_event(self):
        """on_request 回调应收到 confirm_request 事件。"""
        received = []
        from alfred.server.session import ConfirmBridge
        bridge = ConfirmBridge(on_request=received.append, timeout_s=5)

        result = {}
        thread = self._run_confirm(bridge, result)
        time.sleep(0.1)

        assert len(received) == 1
        evt = received[0]
        assert evt["type"] == "confirm_request"
        assert evt["tool_name"] == "shell"
        bridge.respond(evt["id"], True)
        thread.join(timeout=5)


# ── run_turn 事件流测试（回归：生成器必须被迭代，否则事件不流出） ──

class TestRunTurn:
    """回归：run_turn 必须迭代 chat_turn_stream 生成器，否则内部 _run 线程不启动、
    事件不流出，浏览器端卡在"思考中…"永不结束。

    不绑定事件循环（_loop is None），_emit_to_queue 走 put_nowait 同步路径，
    无时序依赖、Windows 可运行。
    """

    def _make_session(self, tmp_path):
        from alfred.server.session import WebSession
        # 刻意不调用 bind_loop：_loop 为 None 时 _emit_to_queue 用 put_nowait，
        # 使测试无需真实事件循环即可确定性地断言事件已入队。
        return WebSession(_make_config(tmp_path))

    def _fake_stream(self, events):
        """模仿真实 chat_turn_stream：每个事件既 emit 到 bus（→ _on_event →
        event_queue）又 yield（供 run_turn 排空以阻塞到轮次结束）。"""
        def gen(agent, deps, session, user_input, bus=None):
            b = bus or EventBus()
            for ev in events:
                b.emit(ev)
                yield ev
        return gen

    def test_run_turn_forwards_events_to_queue(self, tmp_path):
        """事件应完整流到 event_queue（修复前：队列始终为空，UI 卡死）。"""
        ws = self._make_session(tmp_path)
        events = [
            TurnStart(session_id="s", user_text="hi"),
            AssistantChunk(session_id="s", delta="hello"),
            TurnEnd(session_id="s", usage=None),
        ]
        with patch("alfred.server.session.chat_turn_stream", self._fake_stream(events)):
            ws.run_turn("hi")

        delivered = []
        while not ws.event_queue.empty():
            delivered.append(ws.event_queue.get_nowait())
        assert [e["type"] for e in delivered] == ["turn_start", "assistant_chunk", "turn_end"]

    def test_run_turn_passes_bus_to_stream(self, tmp_path):
        """必须把 bus 传给 chat_turn_stream，否则 _on_event 收不到任何事件。"""
        ws = self._make_session(tmp_path)
        seen = {}

        def gen(agent, deps, session, user_input, bus=None):
            seen["bus"] = bus
            yield TurnEnd(session_id="s", usage=None)

        with patch("alfred.server.session.chat_turn_stream", gen):
            ws.run_turn("hi")
        assert seen.get("bus") is not None, "run_turn 必须把 bus 传给 chat_turn_stream"

    def test_turn_error_not_duplicated_on_re_raise(self, tmp_path):
        """生成器发过 TurnError 事件后又抛异常时，不应重复上报 turn_error。"""
        ws = self._make_session(tmp_path)

        def gen(agent, deps, session, user_input, bus=None):
            b = bus or EventBus()
            ev = TurnError(session_id="s", error="boom")
            b.emit(ev)
            yield ev
            raise RuntimeError("boom")

        with patch("alfred.server.session.chat_turn_stream", gen):
            ws.run_turn("hi")  # 异常已在内部消化，不应向外抛出

        delivered = []
        while not ws.event_queue.empty():
            delivered.append(ws.event_queue.get_nowait())
        assert [e["type"] for e in delivered] == ["turn_error"]

    def test_error_before_any_event_is_reported(self, tmp_path):
        """生成器尚未发出任何事件就抛异常时，必须上报 turn_error（如压缩阶段失败）。"""
        ws = self._make_session(tmp_path)

        def gen(agent, deps, session, user_input, bus=None):
            raise RuntimeError("compaction failed")
            yield  # 使 gen 成为生成器函数

        with patch("alfred.server.session.chat_turn_stream", gen):
            ws.run_turn("hi")

        delivered = []
        while not ws.event_queue.empty():
            delivered.append(ws.event_queue.get_nowait())
        assert [e["type"] for e in delivered] == ["turn_error"]
        assert delivered[0]["error"] == "compaction failed"

    def test_run_turn_noop_when_closed(self, tmp_path):
        """close() 后 run_turn 应直接返回，不启动轮次、不产生事件。"""
        calls = {}
        ws = self._make_session(tmp_path)
        ws.close()

        def gen(agent, deps, session, user_input, bus=None):
            calls["called"] = True
            yield

        with patch("alfred.server.session.chat_turn_stream", gen):
            ws.run_turn("hi")
        assert "called" not in calls
        assert ws.event_queue.empty()
