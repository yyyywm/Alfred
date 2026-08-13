"""Embedding 层测试（不调用真实模型/API）。"""

from alfred.config import Config
from alfred.knowledge.embed import OpenAICompatEmbedder, _get_embedder


def test_get_embedder_caches_by_embed_config():
    """相同 embed 配置应返回同一个 Embedder 实例；配置变化则重建。"""
    cfg = Config(
        models={
            "embed": {
                "provider": "openai_compat",
                "name": "m1",
                "base_url": "http://localhost/v1",
                "api_key": "sk-test",
            }
        }
    )
    e1 = _get_embedder(cfg)
    e2 = _get_embedder(cfg)
    assert e1 is e2

    other_cfg = Config(
        models={
            "embed": {
                "provider": "openai_compat",
                "name": "m2",
                "base_url": "http://localhost/v1",
                "api_key": "sk-test",
            }
        }
    )
    e3 = _get_embedder(other_cfg)
    assert e3 is not e1
    assert e3._model == "m2"
