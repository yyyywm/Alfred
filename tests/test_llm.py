"""模型层测试。"""
import time

from pydantic_ai import Agent

from alfred.config import Config
from alfred.llm import list_models, test_model_connection


def test_list_models_key_ready(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-test")
    cfg = Config(providers={
        "p": {"type": "openai_compat", "env_key": "TEST_KEY", "models": ["m1", "m2"]},
    })
    rows = list_models(cfg)
    assert rows == [("p:m1", "openai_compat", True), ("p:m2", "openai_compat", True)]


def test_list_models_key_missing():
    cfg = Config(providers={
        "p": {"type": "openai_compat", "env_key": "MISSING_KEY", "models": ["m1"]},
    })
    rows = list_models(cfg)
    assert rows == [("p:m1", "openai_compat", False)]


def _make_run_sync_mock(ok: bool, latency: float = 0.01):
    def run_sync(self, *args, **kwargs):
        time.sleep(latency)
        if ok:
            class FakeResult:
                output = "OK"
            return FakeResult()
        raise Exception("401 Unauthorized")
    return run_sync


def test_test_model_connection_ok(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-test")
    monkeypatch.setattr(Agent, "run_sync", _make_run_sync_mock(ok=True, latency=0.01))
    cfg = Config(providers={
        "p": {"type": "openai_compat", "base_url": "https://example.com",
              "env_key": "TEST_KEY", "models": ["m1"]},
    })
    result = test_model_connection(cfg, "p:m1")
    assert result["ok"] is True
    assert result["error"] is None
    assert isinstance(result["latency_ms"], float)
    assert result["latency_ms"] > 0


def test_test_model_connection_fail(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-test")
    monkeypatch.setattr(Agent, "run_sync", _make_run_sync_mock(ok=False))
    cfg = Config(providers={
        "p": {"type": "openai_compat", "base_url": "https://example.com",
              "env_key": "TEST_KEY", "models": ["m1"]},
    })
    result = test_model_connection(cfg, "p:m1")
    assert result["ok"] is False
    assert result["error"] is not None
    assert "401" in result["error"] or "Unauthorized" in result["error"]
