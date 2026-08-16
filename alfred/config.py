"""配置加载：config.yaml + .env。

设计依据：模型无关 = 声明式 provider 配置 + 统一解析（llm.py）。
记忆写入路径（memory_write）与闲聊路径（chat）分离——前者固定强模型。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config.yaml"


class ProviderConfig(BaseModel):
    type: Literal["openai_compat", "anthropic", "gemini"] = "openai_compat"
    base_url: str | None = None
    env_key: str = ""
    models: list[str] = []

    def api_key(self) -> str | None:
        if not self.env_key:
            return None
        key = os.environ.get(self.env_key)
        if not key:
            raise KeyError(
                f"环境变量 {self.env_key} 未设置。请在 .env 中填入对应 API key。"
            )
        return key


class EmbedConfig(BaseModel):
    # provider: local（默认本地 sentence-transformers）或 openai_compat（云端 embedding API）
    provider: Literal["local", "openai_compat"] = "local"
    name: str = "Qwen/Qwen3-Embedding-0.6B"
    device: str | None = None
    # 本地模型相关
    hf_endpoint: str | None = None  # 镜像地址，如 https://hf-mirror.com
    local_dir: str | None = None    # 已下载的本地模型目录
    # 云端 API 相关
    base_url: str | None = None
    env_key: str = ""             # 从 .env 读取 API key 的变量名
    api_key: str | None = None     # 直接写死的 key（不推荐，env_key 优先）
    batch_size: int = 64           # 调用 API 时单次请求最大文本数
    dims: int | None = None        # 输出向量维度（用于 mem0/Qdrant 向量库配置）

    def resolve_api_key(self) -> str | None:
        if self.api_key:
            return self.api_key
        if self.env_key:
            key = os.environ.get(self.env_key)
            if not key:
                raise KeyError(
                    f"embedding provider 需要环境变量 {self.env_key}。请在 .env 中填入对应 API key。"
                )
            return key
        return None


class ModelsConfig(BaseModel):
    chat: str = "deepseek:deepseek-chat"
    memory_write: str = "deepseek:deepseek-chat"
    embed: EmbedConfig = EmbedConfig()


class MemoryConfig(BaseModel):
    dir: str = "data/memory"
    block_char_limit: int = 2000
    recall_budget: int = 10
    recency_half_life_days: int = 30
    # 记忆客户端 provider 选择（多 agent 共享 / 云端迁移用）
    provider: Literal["local"] = "local"
    # 默认用户 id：不同 agent / 用户共享记忆基础设施时用于租户隔离
    default_user_id: str = "owner"


class PathsConfig(BaseModel):
    history_dir: str = "data/history"
    vectordb_dir: str = "data/vectordb"
    skills_dirs: list[str] = ["~/.agents/skills"]
    rules_dirs: list[str] = ["rules"]


class Config(BaseModel):
    providers: dict[str, ProviderConfig] = {}
    models: ModelsConfig = ModelsConfig()
    memory: MemoryConfig = MemoryConfig()
    paths: PathsConfig = PathsConfig()

    def resolve(self, model_ref: str) -> tuple[str, ProviderConfig, str]:
        """把 'provider:model' 解析为 (provider_name, provider_config, model)。"""
        if ":" not in model_ref:
            raise ValueError(
                f"模型引用 '{model_ref}' 缺少 provider 前缀，格式应为 provider:model"
            )
        provider_name, model = model_ref.split(":", 1)
        if provider_name not in self.providers:
            raise KeyError(
                f"provider '{provider_name}' 未在 config.yaml 的 providers 中声明"
            )
        return provider_name, self.providers[provider_name], model

    def path(self, p: str) -> Path:
        """配置中的相对路径基于项目根解析，~ 展开。"""
        expanded = Path(os.path.expanduser(p))
        return expanded if expanded.is_absolute() else PROJECT_ROOT / expanded


def load_config(path: Path | None = None) -> Config:
    load_dotenv(PROJECT_ROOT / ".env")
    cfg_path = path or DEFAULT_CONFIG
    if not cfg_path.exists():
        return Config()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return Config(**raw)
