"""The request pipeline that sits behind the HTTP API."""

from __future__ import annotations

from collections.abc import AsyncIterator

from switchboard.config import GatewayConfig
from switchboard.context import RequestContext
from switchboard.providers import Provider, build_provider
from switchboard.routing.router import Router
from switchboard.schemas import ChatCompletionChunk, ChatCompletionRequest, ChatCompletionResponse


class Gateway:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self.providers: dict[str, Provider] = {
            name: build_provider(name, cfg) for name, cfg in config.providers.items()
        }
        self.router = Router(config, self.providers)

    def models(self) -> list[str]:
        return sorted(self.config.models)

    def health(self) -> list[dict[str, str]]:
        return self.router.health()

    async def complete(
        self, req: ChatCompletionRequest, ctx: RequestContext
    ) -> ChatCompletionResponse:
        response = await self.router.complete(req, ctx)
        # Clients asked for the alias; don't leak the upstream model name.
        response.model = req.model
        return response

    async def stream(
        self, req: ChatCompletionRequest, ctx: RequestContext
    ) -> AsyncIterator[ChatCompletionChunk]:
        async for chunk in self.router.stream(req, ctx):
            chunk.model = req.model
            yield chunk

    async def aclose(self) -> None:
        for provider in self.providers.values():
            await provider.aclose()
