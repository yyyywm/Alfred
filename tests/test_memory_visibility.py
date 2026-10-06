"""记忆层失败可见化测试（不调用真实 LLM/embedding/mem0）。

设计依据：operator-memory 的 "Failure Is Visible"——故障必须留在可检查
的地方。旧实现 _init_failed 只是一个布尔位，记忆层静默降级后用户可能
聊很久才发现记忆根本没写入（mem0 Qdrant .lock 残留是最常见根因）。
"""

from pydantic_ai import RunContext

from alfred import agent as agent_mod
from alfred.config import Config, ProviderConfig
from alfred.memory import longterm


def setup_function():
    longterm.reset_clients()


def teardown_function():
    longterm.reset_clients()


def _cfg(tmp_path) -> Config:
    return Config(
        providers={
            "p": ProviderConfig(
                type="openai_compat", base_url="https://example.com/v1",
                env_key="", models=["m"],
            )
        },
        models={"chat": "p:m", "memory_write": "p:m",
                "embed": {"provider": "local", "name": "BAAI/bge-large-zh-v1.5"}},
        memory={"dir": str(tmp_path / "mem")},
        paths={"history_dir": str(tmp_path / "hist"), "vectordb_dir": str(tmp_path / "vdb")},
    )


def test_init_status_records_failure_reason(tmp_path, monkeypatch):
    """初始化失败后 init_status 返回带异常类型的原因，而不是只置布尔位。"""
    def boom(config, user_id):
        raise RuntimeError("qdrant exploded")

    monkeypatch.setattr(longterm, "_make_client", boom)
    cfg = _cfg(tmp_path)

    assert longterm.init_status() is None
    assert longterm.get_client(cfg) is None
    status = longterm.init_status()
    assert status is not None
    assert "RuntimeError" in status and "qdrant exploded" in status


def test_init_status_cleared_on_reset(tmp_path, monkeypatch):
    """reset_clients 必须连失败状态一起清，否则重试永远带着旧错误。"""
    monkeypatch.setattr(
        longterm, "_make_client",
        lambda config, user_id: (_ for _ in ()).throw(ValueError("bad")),
    )
    cfg = _cfg(tmp_path)
    assert longterm.get_client(cfg) is None
    assert longterm.init_status() is not None

    longterm.reset_clients()
    assert longterm.init_status() is None


def test_lock_retry_success_leaves_no_error(tmp_path, monkeypatch):
    """Qdrant 锁清理后重试成功：不留下失败状态（旧实现也只有布尔位，语义相同）。"""
    attempts = []

    class FakeClient:
        pass

    def flaky(config, user_id):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("already accessed by another instance")
        return FakeClient()

    monkeypatch.setattr(longterm, "_make_client", flaky)
    monkeypatch.setattr(longterm, "_clear_qdrant_lock", lambda config: True)
    cfg = _cfg(tmp_path)

    client = longterm.get_client(cfg)
    assert isinstance(client, FakeClient)
    assert longterm.init_status() is None


def test_peek_client_never_triggers_init(tmp_path, monkeypatch):
    """peek_client 只读缓存：未初始化返回 None，且绝不触发 client 构建。"""
    built = []
    monkeypatch.setattr(
        longterm, "_make_client",
        lambda config, user_id: built.append(user_id) or object(),
    )
    cfg = _cfg(tmp_path)

    assert longterm.peek_client(cfg) is None
    assert built == []  # 诊断视图不能把 600MB embedding 下载勾出来

    longterm.get_client(cfg)
    assert longterm.peek_client(cfg) is not None
    assert built == ["owner"]


def _tool_function(agent, name: str):
    tool = agent.toolsets[0].tools[name]
    fn = tool.function
    return getattr(fn, "__wrapped__", fn)


def test_memory_search_reports_offline_instead_of_empty(tmp_path, monkeypatch):
    """memory_search 在记忆层离线时返回明确诊断，不静默伪装成"没有记忆"。"""
    cfg = _cfg(tmp_path)
    longterm.reset_clients()
    monkeypatch.setattr(longterm, "get_client", lambda *a, **k: None)
    monkeypatch.setattr(
        longterm, "init_status", lambda: "RuntimeError: qdrant exploded",
    )

    agent = agent_mod.build_agent(cfg)
    search = _tool_function(agent, "memory_search")
    deps = agent_mod.AlfredDeps(config=cfg, blocks=None, last_recalled=[])
    result = search(RunContext(deps=deps, model=None, usage=None), "喜好")

    assert "离线" in result
    assert "qdrant exploded" in result
    assert deps.last_recalled == []  # 离线不是召回，/why 不应出现假依据
