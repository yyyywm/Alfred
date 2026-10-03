# tests/test_web.py
"""web_search / web_fetch 联网工具测试：纯逻辑 + agent 集成，不访问真实网络。"""
import httpx
import pytest
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

import alfred.web
from alfred.agent import AlfredDeps, build_agent, chat_turn_stream
from alfred.config import Config, ProviderConfig
from alfred.events import EventBus, ToolCallEnd
from alfred.history import Session
from alfred.web import WebError, fetch_webpage, search_web

_BING_HTML = """
<html><body>
<ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://arxiv.org/abs/2112.07957">FEAR: Fast, Efficient, Accurate and Robust Visual Tracker</a></h2>
    <p>ECCV 2022 paper about dual-template Siamese tracking.</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://github.com/PinataFarms/FEARTracker">PinataFarms/FEARTracker · GitHub</a></h2>
    <p>Official implementation.</p>
  </li>
</ol>
</body></html>
"""

_PAGE_HTML = """
<html>
<head><title>Demo</title><style>body{color:red}</style></head>
<body>
<script>var x = 1; alert(x);</script>
<h1>双模板结构</h1>
<p>静态模板锚定第一帧外观，</p>
<p>动态模板在线更新。</p>
<noscript>请开启 JS</noscript>
</body>
</html>
"""


def _test_config(tmp_path, web: dict | None = None):
    return Config(
        providers={"dummy": ProviderConfig(type="openai_compat", models=["m"])},
        models={"chat": "dummy:m", "memory_write": "dummy:m"},
        paths={"history_dir": str(tmp_path / "hist")},
        web=web or {},
    )


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


# ── bing 后端 ────────────────────────────────────────────────────────


def test_bing_search_parses_results(tmp_path):
    cfg = _test_config(tmp_path, web={"search_provider": "bing"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert "bing.com/search" in str(request.url)
        return httpx.Response(200, text=_BING_HTML)

    results = search_web(cfg, "FEAR tracker", client=_mock_client(handler))

    assert len(results) == 2
    assert results[0]["title"].startswith("FEAR")
    assert results[0]["url"] == "https://arxiv.org/abs/2112.07957"
    assert "dual-template" in results[0]["snippet"]


def test_bing_search_no_results_raises(tmp_path):
    cfg = _test_config(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html><body></body></html>")

    with pytest.raises(WebError, match="没有解析到结果"):
        search_web(cfg, "xyz", client=_mock_client(handler))


def test_bing_search_http_error_raises(tmp_path):
    cfg = _test_config(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="blocked")

    with pytest.raises(WebError, match="503"):
        search_web(cfg, "xyz", client=_mock_client(handler))


# ── tavily 后端 ──────────────────────────────────────────────────────


def test_tavily_search_maps_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    cfg = _test_config(tmp_path, web={"search_provider": "tavily"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert "api.tavily.com" in str(request.url)
        return httpx.Response(200, json={
            "results": [
                {"title": "T1", "url": "https://a.com", "content": "snippet-a"},
                {"title": "T2", "url": "https://b.com", "content": "snippet-b"},
            ]
        })

    results = search_web(cfg, "q", client=_mock_client(handler))

    assert results == [
        {"title": "T1", "url": "https://a.com", "snippet": "snippet-a"},
        {"title": "T2", "url": "https://b.com", "snippet": "snippet-b"},
    ]


def test_tavily_without_key_falls_back_to_bing(tmp_path, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    cfg = _test_config(tmp_path, web={"search_provider": "tavily"})
    called = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(str(request.url))
        return httpx.Response(200, text=_BING_HTML)

    results = search_web(cfg, "FEAR", client=_mock_client(handler))

    assert results and "bing.com" in called[0]


# ── fetch_webpage ────────────────────────────────────────────────────


def test_fetch_extracts_visible_text(tmp_path):
    cfg = _test_config(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_PAGE_HTML,
                              headers={"content-type": "text/html; charset=utf-8"})

    text = fetch_webpage(cfg, "https://example.com/p", client=_mock_client(handler))

    assert "双模板结构" in text
    assert "动态模板在线更新" in text
    # script/style/noscript 内容应被剔除
    assert "alert" not in text
    assert "color:red" not in text
    assert "请开启 JS" not in text


def test_fetch_truncates_long_page(tmp_path):
    cfg = _test_config(tmp_path, web={"fetch_max_chars": 100})
    long_body = "<html><body><p>" + "很长的正文。" * 200 + "</p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=long_body,
                              headers={"content-type": "text/html"})

    text = fetch_webpage(cfg, "https://example.com/long", client=_mock_client(handler))

    assert "[内容已截断，原始页面: https://example.com/long]" in text


def test_fetch_rejects_non_html(tmp_path):
    cfg = _test_config(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.4 ...",
                              headers={"content-type": "application/pdf"})

    with pytest.raises(WebError, match="不是 HTML 网页"):
        fetch_webpage(cfg, "https://example.com/a.pdf", client=_mock_client(handler))


def test_fetch_rejects_bad_scheme(tmp_path):
    cfg = _test_config(tmp_path)
    with pytest.raises(WebError, match="http"):
        fetch_webpage(cfg, "ftp://example.com/x")


# ── agent 集成 ───────────────────────────────────────────────────────


def _make_web_search_stream():
    async def stream(messages, info):
        has_tool_return = any(
            getattr(m, "parts", None) and any(
                isinstance(p, ToolReturnPart) for p in m.parts
            )
            for m in messages
        )
        if has_tool_return:
            yield "查到了，来源已标注。"
            return
        yield {0: DeltaToolCall(
            name="web_search", json_args='{"query":"FEAR ECCV 2022"}',
            tool_call_id="tc-web-1",
        )}

    return stream


def test_agent_registers_and_calls_web_search(tmp_path, monkeypatch):
    cfg = _test_config(tmp_path)
    agent = build_agent(cfg)
    tool_names = agent.toolsets[0].tools
    assert "web_search" in tool_names
    assert "web_fetch" in tool_names

    fake_results = [{
        "title": "FEAR: Fast, Efficient, Accurate and Robust Visual Tracker",
        "url": "https://arxiv.org/abs/2112.07957",
        "snippet": "ECCV 2022, dual-template tracker.",
    }]
    monkeypatch.setattr(
        alfred.web, "search_web",
        lambda config, query, limit=None, client=None: fake_results,
    )
    agent.model = FunctionModel(stream_function=_make_web_search_stream())

    session = Session(cfg)
    deps = AlfredDeps(config=cfg, blocks=None, confirm=lambda _msg, **_kw: True)
    events = list(chat_turn_stream(agent, deps, session, "查一下 FEAR", bus=EventBus()))

    ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(ends) == 1
    assert ends[0].tool_name == "web_search"
    assert not ends[0].is_error
    assert "arxiv.org/abs/2112.07957" in ends[0].result


def test_agent_web_search_failure_is_friendly(tmp_path, monkeypatch):
    cfg = _test_config(tmp_path)
    agent = build_agent(cfg)

    def _boom(config, query, limit=None, client=None):
        raise WebError("搜索请求失败：连接超时")

    monkeypatch.setattr(alfred.web, "search_web", _boom)
    agent.model = FunctionModel(stream_function=_make_web_search_stream())

    session = Session(cfg)
    deps = AlfredDeps(config=cfg, blocks=None, confirm=lambda _msg, **_kw: True)
    events = list(chat_turn_stream(agent, deps, session, "查一下", bus=EventBus()))

    ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(ends) == 1
    assert not ends[0].is_error  # 工具内部消化异常，返回可操作文案
    assert "搜索暂不可用" in ends[0].result
