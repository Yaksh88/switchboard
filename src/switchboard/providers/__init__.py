from __future__ import annotations

from switchboard.config import ProviderConfig
from switchboard.providers.anthropic import AnthropicProvider
from switchboard.providers.base import Provider
from switchboard.providers.mock import MockProvider
from switchboard.providers.openai import OpenAIProvider

_REGISTRY: dict[str, type[Provider]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "mock": MockProvider,
}


def build_provider(name: str, config: ProviderConfig) -> Provider:
    return _REGISTRY[config.type](name, config)


__all__ = ["AnthropicProvider", "MockProvider", "OpenAIProvider", "Provider", "build_provider"]
