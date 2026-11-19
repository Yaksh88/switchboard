"""Configuration.

Two layers:

* ``Settings`` — process-level settings from environment variables (12-factor).
* ``GatewayConfig`` — declarative routing config (providers + model aliases) loaded
  from YAML. ``${VAR}`` and ``${VAR:-default}`` references are expanded from the
  environment so secrets never live in the file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderType = Literal["openai", "anthropic", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SWITCHBOARD_", env_file=".env", extra="ignore")

    config_path: Path = Path("config/switchboard.yaml")
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"
    redis_url: str = "redis://localhost:6379/0"


class ProviderConfig(BaseModel):
    type: ProviderType
    api_key: str | None = None
    base_url: str | None = None
    timeout_s: float = 60.0
    max_retries: int = Field(default=1, ge=0, le=5)
    # Mock-only knobs, useful for demos and chaos testing.
    latency_ms: int = 0
    failure_rate: float = Field(default=0.0, ge=0, le=1)


class Target(BaseModel):
    """A concrete (provider, upstream model) pair a public model alias can resolve to."""

    provider: str
    model: str


class ModelConfig(BaseModel):
    targets: list[Target] = Field(min_length=1)


class SemanticCacheConfig(BaseModel):
    enabled: bool = False
    threshold: float = Field(default=0.92, gt=0, le=1)
    max_entries: int = Field(default=1000, gt=0)
    embedder: Literal["hashing", "openai"] = "hashing"
    # Provider (type: openai) used to compute embeddings when embedder is "openai".
    embedding_provider: str | None = None
    embedding_model: str = "text-embedding-3-small"

    @model_validator(mode="after")
    def _openai_embedder_needs_provider(self) -> SemanticCacheConfig:
        if self.embedder == "openai" and not self.embedding_provider:
            raise ValueError("embedding_provider is required when embedder is 'openai'")
        return self


class CacheConfig(BaseModel):
    enabled: bool = True
    ttl_s: int = Field(default=3600, gt=0)
    semantic: SemanticCacheConfig = SemanticCacheConfig()


class GatewayConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelConfig]
    cache: CacheConfig = CacheConfig()

    @model_validator(mode="after")
    def _targets_reference_known_providers(self) -> GatewayConfig:
        for alias, model in self.models.items():
            for target in model.targets:
                if target.provider not in self.providers:
                    raise ValueError(
                        f"model '{alias}' references unknown provider '{target.provider}'"
                    )
        return self


_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_REF.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load_gateway_config(path: Path) -> GatewayConfig:
    raw = yaml.safe_load(path.read_text()) or {}
    return GatewayConfig.model_validate(_expand_env(raw))
