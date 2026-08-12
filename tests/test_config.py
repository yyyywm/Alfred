"""配置层测试。"""

import os

from alfred.config import Config, load_config


def test_default_config():
    cfg = Config()
    assert cfg.memory.block_char_limit == 2000
    assert cfg.memory.recall_budget == 10


def test_resolve_model_ref():
    cfg = Config(providers={
        "deepseek": {"type": "openai_compat", "base_url": "https://api.deepseek.com",
                      "env_key": "", "models": ["deepseek-chat"]},
    })
    name, provider, model = cfg.resolve("deepseek:deepseek-chat")
    assert name == "deepseek" and model == "deepseek-chat"


def test_resolve_invalid_ref():
    cfg = Config()
    try:
        cfg.resolve("no-colon-here")
        assert False, "应抛出 ValueError"
    except ValueError:
        pass
    try:
        cfg.resolve("unknown:model")
        assert False, "应抛出 KeyError"
    except KeyError:
        pass


def test_env_key_resolution(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "sk-test-123")
    cfg = Config(providers={
        "p": {"type": "openai_compat", "env_key": "TEST_API_KEY", "models": ["m"]},
    })
    _, provider, _ = cfg.resolve("p:m")
    assert provider.api_key() == "sk-test-123"


def test_env_key_missing():
    os.environ.pop("DEFINITELY_MISSING_KEY", None)
    cfg = Config(providers={
        "p": {"type": "openai_compat", "env_key": "DEFINITELY_MISSING_KEY", "models": ["m"]},
    })
    _, provider, _ = cfg.resolve("p:m")
    try:
        provider.api_key()
        assert False, "应抛出 KeyError"
    except KeyError as e:
        assert "DEFINITELY_MISSING_KEY" in str(e)


def test_load_project_config():
    cfg = load_config()
    assert cfg.providers, "项目 config.yaml 应至少声明一个 provider"
    assert ":" in cfg.models.chat
