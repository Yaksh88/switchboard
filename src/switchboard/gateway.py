"""The request pipeline that sits behind the HTTP API."""

from __future__ import annotations

from collections.abc import AsyncIterator

from switchboard.config import GatewayConfig
from switchboard.errors import ModelNotFoundError
from switchboard.providers import Provider, build_provider
from switchboard.schemas import ChatCompletionChunk, ChatCompletionRequest, ChatCompletionResponse


class Gateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.providers: dict[str, Provider] = {
            name: build_provider(name, cfg) for name, cfg in config.providers.items()
        }

    def models(self) -> list[str]:
        return sorted(self.config.models)

    def _resolve(self, alias: str) -> tuple[Provider, str]:
        model = self.config.models.get(alias)
        if model is None:
            raise ModelNotFoundError(alias)
        target = model.targets[0]
        return self.providers[target.provider], target.model

    async def complete(self, req: ChatCompletionRequest) -> ChatCompletionResponse:
        provider, upstream = self._resolve(req.model)
        response = await provider.complete(req, upstream)
        # Clients asked for the alias; don't leak the upstream model name.
        response.model = req.model
        return response

    async def stream(self, req: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]:
        provider, upstream = self._resolve(req.model)
        async for chunk in provider.stream(req, upstream):
            chunk.model = req.model
            yield chunk

    async def aclose(self) -> None:
        for provider in self.providers.values():
            await provider.aclose()
