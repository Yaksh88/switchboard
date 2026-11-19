"""The request pipeline that sits behind the HTTP API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from redis.asyncio import Redis
from redis.exceptions import RedisError

from switchboard.cache.embeddings import Embedder, HashingEmbedder, OpenAIEmbedder
from switchboard.cache.exact import ExactCache
from switchboard.cache.keys import is_cacheable
from switchboard.cache.replay import replay_as_chunks
from switchboard.cache.semantic import SemanticCache
from switchboard.config import GatewayConfig
from switchboard.context import RequestContext
from switchboard.errors import GatewayError
from switchboard.providers import Provider, build_provider
from switchboard.providers.base import HttpProvider
from switchboard.routing.router import Router
from switchboard.schemas import (
    AssistantMessage,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    FinishReason,
    Usage,
)

log = logging.getLogger("switchboard.gateway")


class Gateway:
    def __init__(self, config: GatewayConfig, redis: Redis | None = None) -> None:
        self.config = config
        self.providers: dict[str, Provider] = {
            name: build_provider(name, cfg) for name, cfg in config.providers.items()
        }
        self.router = Router(config, self.providers)
        self.exact: ExactCache | None = None
        self.semantic: SemanticCache | None = None

        cache = config.cache
        if redis is not None and cache.enabled:
            self.exact = ExactCache(redis, cache.ttl_s)
            if cache.semantic.enabled:
                self.semantic = SemanticCache(
                    redis,
                    self._build_embedder(),
                    threshold=cache.semantic.threshold,
                    max_entries=cache.semantic.max_entries,
                    ttl_s=cache.ttl_s,
                )

    def _build_embedder(self) -> Embedder:
        settings = self.config.cache.semantic
        if settings.embedder == "hashing":
            return HashingEmbedder()
        provider = self.providers.get(settings.embedding_provider or "")
        if not isinstance(provider, HttpProvider):
            raise ValueError(
                f"embedding_provider '{settings.embedding_provider}' must be an HTTP provider"
            )
        return OpenAIEmbedder(provider.client, settings.embedding_model, provider.name)

    def models(self) -> list[str]:
        return sorted(self.config.models)

    def health(self) -> list[dict[str, str]]:
        return self.router.health()

    async def _lookup(
        self, req: ChatCompletionRequest, ctx: RequestContext
    ) -> ChatCompletionResponse | None:
        if self.exact is None or ctx.bypass_cache or not is_cacheable(req):
            return None
        ctx.cache = "miss"
        # The cache is an optimisation: if Redis or the embedder is down, serve uncached.
        try:
            if (hit := await self.exact.get(req)) is not None:
                ctx.cache = "exact"
                return hit
            if self.semantic is not None and (similar := await self.semantic.get(req)):
                ctx.cache = "semantic"
                return similar[0]
        except (RedisError, GatewayError, OSError):
            log.warning("cache lookup failed; serving uncached", exc_info=True)
        return None

    async def _store(
        self, req: ChatCompletionRequest, resp: ChatCompletionResponse, ctx: RequestContext
    ) -> None:
        if ctx.cache != "miss" or self.exact is None:
            return
        try:
            await self.exact.set(req, resp)
            if self.semantic is not None:
                await self.semantic.set(req, resp)
        except (RedisError, GatewayError, OSError):
            log.warning("cache store failed", exc_info=True)

    @staticmethod
    def _serve_hit(
        hit: ChatCompletionResponse, req: ChatCompletionRequest, ctx: RequestContext
    ) -> ChatCompletionResponse:
        ctx.provider = "cache"
        ctx.usage = hit.usage
        return hit.model_copy(update={"model": req.model})

    async def complete(
        self, req: ChatCompletionRequest, ctx: RequestContext
    ) -> ChatCompletionResponse:
        if (hit := await self._lookup(req, ctx)) is not None:
            return self._serve_hit(hit, req, ctx)
        response = await self.router.complete(req, ctx)
        # Clients asked for the alias; don't leak the upstream model name.
        response.model = req.model
        ctx.usage = response.usage
        await self._store(req, response, ctx)
        return response

    async def stream(
        self, req: ChatCompletionRequest, ctx: RequestContext
    ) -> AsyncIterator[ChatCompletionChunk]:
        if (hit := await self._lookup(req, ctx)) is not None:
            async for chunk in replay_as_chunks(self._serve_hit(hit, req, ctx)):
                yield chunk
            return

        parts: list[str] = []
        usage: Usage | None = None
        finish: FinishReason | None = None
        async for chunk in self.router.stream(req, ctx):
            chunk.model = req.model
            for choice in chunk.choices:
                parts.append(choice.delta.content or "")
                finish = choice.finish_reason or finish
            usage = chunk.usage or usage
            yield chunk

        # Reached only when the stream finished; a disconnect or error never caches a partial.
        ctx.usage = usage
        await self._store(
            req,
            ChatCompletionResponse(
                model=req.model,
                choices=[
                    Choice(
                        message=AssistantMessage(content="".join(parts)),
                        finish_reason=finish or "stop",
                    )
                ],
                usage=usage or Usage(),
            ),
            ctx,
        )

    async def aclose(self) -> None:
        for provider in self.providers.values():
            await provider.aclose()
