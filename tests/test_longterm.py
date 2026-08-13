"""长期记忆层测试（不调用真实 LLM/embedding）。"""

from alfred.config import Config
from alfred.memory import longterm


def test_build_mem0_uses_openai_embedder_for_openai_compat(monkeypatch):
    """当 config embed provider 为 openai_compat 时，mem0 应使用 openai embedder，而不是去下载 huggingface 模型。"""
    captured = {}

    class FakeMemory:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        @classmethod
        def from_config(cls, cfg):
            return cls(cfg)

    monkeypatch.setattr("mem0.Memory", FakeMemory)

    # 重置单例状态
    longterm._mem = None
    longterm._init_failed = False

    cfg = Config(
        providers={
            "p": {
                "type": "openai_compat",
                "base_url": "https://example.com/v1",
                "env_key": "",
                "models": ["m"],
            }
        },
        models={
            "chat": "p:m",
            "memory_write": "p:m",
            "embed": {
                "provider": "openai_compat",
                "name": "BAAI/bge-large-zh-v1.5",
                "base_url": "https://api.siliconflow.cn/v1",
                "api_key": "sk-test",
            },
        },
    )

    mem = longterm.get_memory(cfg)
    assert mem is not None
    embedder_cfg = captured["cfg"]["embedder"]
    assert embedder_cfg["provider"] == "openai"
    assert embedder_cfg["config"]["model"] == "BAAI/bge-large-zh-v1.5"
    assert embedder_cfg["config"]["api_key"] == "sk-test"
    assert embedder_cfg["config"]["openai_base_url"] == "https://api.siliconflow.cn/v1"


def test_build_mem0_uses_huggingface_embedder_for_local(monkeypatch):
    """当 config embed provider 为 local 时，mem0 仍使用 huggingface embedder。"""
    captured = {}

    class FakeMemory:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        @classmethod
        def from_config(cls, cfg):
            return cls(cfg)

    monkeypatch.setattr("mem0.Memory", FakeMemory)
    longterm._mem = None
    longterm._init_failed = False

    cfg = Config(
        providers={
            "p": {
                "type": "openai_compat",
                "base_url": "https://example.com/v1",
                "env_key": "",
                "models": ["m"],
            }
        },
        models={
            "chat": "p:m",
            "memory_write": "p:m",
            "embed": {
                "provider": "local",
                "name": "BAAI/bge-large-zh-v1.5",
            },
        },
    )

    mem = longterm.get_memory(cfg)
    assert mem is not None
    embedder_cfg = captured["cfg"]["embedder"]
    assert embedder_cfg["provider"] == "huggingface"
    assert embedder_cfg["config"]["model"] == "BAAI/bge-large-zh-v1.5"
