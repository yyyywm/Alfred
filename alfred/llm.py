"""模型解析：config.yaml 的 provider:model → pydantic-ai model 实例。

模型无关的实现策略：
- openai_compat 覆盖所有 OpenAI 兼容端点（DeepSeek/通义/月之暗面/Ollama/LiteLLM Proxy）
- anthropic / gemini 走 pydantic-ai 原生 provider
- 换模型 = 改 config 或 /model 命令，代码零改动
"""

from __future__ import annotations

import time

from pydantic_ai import Agent
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.settings import ModelSettings

try:  # pydantic-ai 2.x：gemini 更名为 google
    from pydantic_ai.models.google import GoogleModel as GeminiModel
    from pydantic_ai.providers.google import GoogleProvider as GeminiProvider
except ImportError:  # 旧版兼容
    from pydantic_ai.models.gemini import GeminiModel
    from pydantic_ai.providers.gemini import GeminiProvider

from .config import Config


def build_model(config: Config, model_ref: str):
    """把 'provider:model' 解析为 pydantic-ai model 实例。"""
    _name, provider, model_name = config.resolve(model_ref)

    if provider.type == "anthropic":
        kwargs = {"api_key": provider.api_key()}
        if provider.base_url:
            kwargs["base_url"] = provider.base_url
        return AnthropicModel(
            model_name,
            provider=AnthropicProvider(**kwargs),
        )
    if provider.type == "gemini":
        return GeminiModel(
            model_name,
            provider=GeminiProvider(api_key=provider.api_key()),
        )
    # openai_compat：所有 OpenAI 兼容端点统一入口
    return OpenAIChatModel(
        model_name,
        provider=OpenAIProvider(
            base_url=provider.base_url,
            api_key=provider.api_key() or "not-needed",
        ),
    )


def list_models(config: Config) -> list[tuple[str, str, bool]]:
    """返回 [(ref, type, key_ready), ...] 供 alfred models 展示。"""
    rows = []
    for name, p in config.providers.items():
        try:
            ready = bool(p.api_key()) or not p.env_key
        except KeyError:
            ready = False
        for m in p.models:
            rows.append((f"{name}:{m}", p.type, ready))
    return rows


def _format_model_error(err: Exception) -> str:
    """把模型调用异常映射为中文友好的提示。"""
    text = str(err)
    msg = text.lower()
    if "401" in text or "unauthorized" in msg or "authentication" in msg:
        return f"401 Unauthorized - API key 无效或缺失（{err}）"
    if "403" in text or "forbidden" in msg:
        return f"403 Forbidden - 权限不足（{err}）"
    if "404" in text or "not found" in msg:
        return f"404 Not Found - 模型名或端点路径错误（{err}）"
    if "timeout" in msg:
        return f"请求超时 - 请检查网络或 base_url（{err}）"
    if "connect" in msg or "network" in msg or "dns" in msg:
        return f"网络连接失败 - 请检查 base_url 和网络（{err}）"
    return f"{type(err).__name__}: {err}"


def check_model_connection(config: Config, model_ref: str, timeout: float = 10.0) -> dict:
    """实际探测模型连通性，返回 ok / latency_ms / error。"""
    start = time.perf_counter()
    try:
        model = build_model(config, model_ref)
        agent = Agent(
            model,
            system_prompt="Reply with only the word OK.",
        )
        agent.run_sync(
            "ping",
            model_settings=ModelSettings(timeout=timeout, max_tokens=5),
        )
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "error": None,
        }
    except Exception as err:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - start) * 1000, 1),
            "error": _format_model_error(err),
        }
