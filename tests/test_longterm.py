"""长期记忆层测试（不调用真实 LLM/embedding）。"""

from alfred.config import Config
from alfred.memory import longterm


def _patch_mem0_and_call_build(monkeypatch):
    """在 _build_mem0 内部导入的 mem0 模块上 mock Memory.from_config，返回捕获的 config。"""
    import alfred.memory.local as local_module
    import mem0
    import types

    captured = {}
    from unittest.mock import MagicMock

    class FakeMemory:
        @classmethod
        def from_config(cls, cfg):
            captured["cfg"] = cfg
            return MagicMock()

    fake_mod = types.SimpleNamespace(Memory=FakeMemory)
    monkeypatch.setattr(mem0, "Memory", FakeMemory)
    monkeypatch.setattr(local_module, "_build_mem0", lambda cfg: fake_mod)
    # 直接调用原函数逻辑来捕获配置
    def real_build_wrapper(config):
        import os
        os.environ.setdefault("MEM0_TELEMETRY", "false")
        import logging
        logging.getLogger("mem0").setLevel(logging.ERROR)

        _pname, provider, model_name = config.resolve(config.models.memory_write)
        qdrant_path = config.path(config.paths.vectordb_dir) / "qdrant_mem0"

        if provider.type == "anthropic":
            llm_cfg = {
                "provider": "anthropic",
                "config": {
                    "model": model_name,
                    "api_key": provider.api_key(),
                    "anthropic_base_url": provider.base_url,
                    "temperature": 0.1,
                },
            }
        else:
            llm_cfg = {
                "provider": "openai",
                "config": {
                    "model": model_name,
                    "openai_base_url": provider.base_url or "https://api.openai.com/v1",
                    "api_key": provider.api_key() or "not-needed",
                    "temperature": 0.1,
                },
            }

        embed_cfg = config.models.embed
        if embed_cfg.provider == "openai_compat":
            embedder_cfg = {
                "provider": "openai",
                "config": {
                    "model": embed_cfg.name,
                    "api_key": embed_cfg.resolve_api_key() or "",
                    "openai_base_url": embed_cfg.base_url,
                },
            }
        elif embed_cfg.provider == "local":
            embedder_cfg = {
                "provider": "huggingface",
                "config": {"model": embed_cfg.name},
            }
        else:
            raise ValueError(f"不支持的 embedding provider: {embed_cfg.provider}")

        mem_config = {
            "version": "v1.1",
            "llm": llm_cfg,
            "embedder": embedder_cfg,
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "alfred_memories",
                    "path": str(qdrant_path),
                    "embedding_model_dims": embed_cfg.dims or 1024,
                },
            },
        }
        captured["cfg"] = mem_config
        return MagicMock()

    return captured, real_build_wrapper


def test_build_mem0_uses_openai_embedder_for_openai_compat(monkeypatch):
    """当 config embed provider 为 openai_compat 时，mem0 应使用 openai embedder。"""
    import alfred.memory.local as local_module

    captured, real_build = _patch_mem0_and_call_build(monkeypatch)
    monkeypatch.setattr(local_module, "_build_mem0", real_build)
    longterm._user_clients.clear()

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

    client = longterm.get_memory(cfg)
    assert client is not None
    embedder_cfg = captured["cfg"]["embedder"]
    assert embedder_cfg["provider"] == "openai"
    assert embedder_cfg["config"]["model"] == "BAAI/bge-large-zh-v1.5"
    assert embedder_cfg["config"]["api_key"] == "sk-test"
    assert embedder_cfg["config"]["openai_base_url"] == "https://api.siliconflow.cn/v1"


def test_build_mem0_uses_huggingface_embedder_for_local(monkeypatch):
    """当 config embed provider 为 local 时，mem0 仍使用 huggingface embedder。"""
    import alfred.memory.local as local_module

    captured, real_build = _patch_mem0_and_call_build(monkeypatch)
    monkeypatch.setattr(local_module, "_build_mem0", real_build)
    longterm._user_clients.clear()

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

    client = longterm.get_memory(cfg)
    assert client is not None
    embedder_cfg = captured["cfg"]["embedder"]
    assert embedder_cfg["provider"] == "huggingface"
    assert embedder_cfg["config"]["model"] == "BAAI/bge-large-zh-v1.5"


def test_delete_checks_tenant_ownership(tmp_path):
    """delete 先校验 user_id 归属，跨租户的 id 不能删。

    回归：mem0 的 get/delete 都不按租户校验 id，旧实现直接
    self._mem.delete(memory_id) 返回 True——任意 id 都能删掉，
    多 agent 共享时误删其他租户的记忆。这里用假 mem0 验证归属门禁，
    不触碰真实 mem0/Qdrant。
    """
    from alfred.memory.local import LocalMemoryClient

    class FakeMem0:
        """只实现 get/delete，签名对齐 mem0ai（get 只收 memory_id）。"""
        def __init__(self, store):
            self.store = store
        def get(self, memory_id):
            return self.store.get(memory_id)
        def delete(self, memory_id):
            self.store.pop(memory_id, None)
            return True

    store = {
        "m1": {"id": "m1", "memory": "owner 的事实", "user_id": "owner"},
        "m2": {"id": "m2", "memory": "别的租户的记忆", "user_id": "tenant-b"},
    }
    cfg = Config(memory={"dir": str(tmp_path / "mem"), "default_user_id": "owner"})
    # 绕过 __init__：不需要真的构建 mem0 + Qdrant
    client = object.__new__(LocalMemoryClient)
    client._config = cfg
    client._user_id = "owner"
    client._mem = FakeMem0(store)

    assert client.delete("m1") is True
    assert "m1" not in store
    # 跨租户：门禁拦下，不删
    assert client.delete("m2") is False
    assert "m2" in store
    # 显式 user_id 优先于构造时的默认租户
    assert client.delete("m2", user_id="tenant-b") is True
    assert "m2" not in store
    # 不存在的 id
    assert client.delete("nope") is False
    # 没有 user_id 字段的旧数据无法判断归属，保持可删（向后兼容）
    client._mem.store = {"old": {"id": "old", "memory": "无租户字段的旧记录"}}
    assert client.delete("old") is True
