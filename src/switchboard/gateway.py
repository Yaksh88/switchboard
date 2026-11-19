"""The request pipeline that sits behind the HTTP API."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from switchboard.auth.budget import BudgetGuard
from switchboard.cache.embeddings import Embedder, HashingEmbedder, OpenAIEmbedder
from switchboard.cache.exact import ExactCache
from switchboard.cache.keys import is_cacheable
from switchboard.cache.replay import replay_as_chunks
from switchboard.cache.semantic import SemanticCache
from switchboard.config import GatewayConfig
from switchboard.context import RequestContext
from switchboard.errors import GatewayError, PermissionDeniedError, RateLimitError
from switchboard.observability import metrics
from switchboard.observability.cost import compute_cost
from switchboard.observability.log_writer import LogWriter
from switchboard.observability.logging import log_request
from switchboard.providers import Provider, build_provider
from switchboard.providers.base import HttpProvider
from switchboard.ratelimit.limiter import RateLimiter
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
from switchboard.tokens import estimate_tokens

log = logging.getLogger("switchboard.gateway")


class Gateway:
    def __init__(
        self,
        config: GatewayConfig,
        redis: Redis | None = None,
        *,
        log_writer: LogWriter | None = None,
        budget: BudgetGuard | None = None,
    ) -> None:
        self.config = config
        self.log_writer = log_writer
        self.budget = budget
        self.providers: dict[str, Provider] = {
            name: build_provider(name, cfg) for name, cfg in config.providers.items()
        }
        self.router = Router(config, self.providers)
        self.limiter = RateLimiter(redis) if redis is not None else None
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

    async def admit(self, req: ChatCompletionRequest, ctx: RequestContext) -> None:
        """Enforce the caller's model allowlist and rate limits.

        Runs before any response bytes are sent so a rejection is a real 403/429, not an
        in-band stream error.
        """
        principal = ctx.principal
        if principal is None:
            return
        if principal.allowed_models is not None and req.model not in principal.allowed_models:
            raise PermissionDeniedError(f"This API key may not use model '{req.model}'")
        if self.budget is not None:
            try:
                await self.budget.check(principal)
            except (SQLAlchemyError, OSError):
                log.warning("budget check unavailable; admitting request", exc_info=True)
        if self.limiter is None:
            return

        # A limiter outage must not take the gateway down, so failures admit the request.
        try:
            if principal.rpm_limit is not None:
                rpm = principal.rpm_limit
                decision = await self.limiter.consume(
                    f"sb:rl:{principal.key_id}:rpm", 1, rpm, rpm / 60
                )
                ctx.ratelimit_remaining = decision.remaining
                if not decision.allowed:
                    raise RateLimitError(
                        f"Rate limit of {rpm} requests per minute exceeded",
                        retry_after_s=decision.retry_after_s,
                        remaining=0,
                    )
            if principal.tpm_limit is not None:
                tpm = principal.tpm_limit
                # Capped at the bucket size: a prompt larger than the whole budget could
                # otherwise never be admitted, however long the caller waits.
                prompt_tokens = min(sum(estimate_tokens(m.text()) for m in req.messages), tpm)
                decision = await self.limiter.consume(
                    f"sb:rl:{principal.key_id}:tpm", prompt_tokens, tpm, tpm / 60
                )
                if not decision.allowed:
                    raise RateLimitError(
                        f"Rate limit of {tpm} tokens per minute exceeded",
                        retry_after_s=decision.retry_after_s,
                    )
        except (RedisError, OSError):
            log.warning("rate limiter unavailable; admitting request", exc_info=True)

    async def _settle(self, ctx: RequestContext) -> None:
        """Charge generated tokens against the TPM bucket once they're known."""
        principal = ctx.principal
        if self.limiter is None or principal is None or principal.tpm_limit is None:
            return
        if ctx.usage is None or ctx.usage.completion_tokens == 0:
            return
        tpm = principal.tpm_limit
        try:
            await self.limiter.debit(
                f"sb:rl:{principal.key_id}:tpm", ctx.usage.completion_tokens, tpm, tpm / 60
            )
        except (RedisError, OSError):
            log.warning("rate limiter unavailable; skipping token debit", exc_info=True)

    def finish(self, ctx: RequestContext) -> None:
        """Account for a finished request: cost, metrics, access log and the request log row.

        Synchronous and non-blocking so it is safe to call from a ``finally`` while a
        cancelled stream unwinds.
        """
        ctx.cost_usd = self._cost(ctx)
        targets = (
            self.config.models[ctx.model_alias].targets
            if ctx.model_alias in self.config.models
            else []
        )
        if targets and ctx.provider not in (None, "cache") and ctx.provider != targets[0].provider:
            metrics.FALLBACKS.labels(ctx.model_alias).inc()
        metrics.record(ctx)
        log_request(ctx)
        if self.log_writer is not None:
            self.log_writer.submit(ctx)

    def _cost(self, ctx: RequestContext) -> float:
        if ctx.usage is None or ctx.cache in ("exact", "semantic"):
            return 0.0
        model = self.config.models.get(ctx.model_alias)
        if model is None:
            return 0.0
        for target in model.targets:
            if target.provider == ctx.provider and target.model == ctx.upstream_model:
                return compute_cost(ctx.usage, target)
        return 0.0

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
            response = self._serve_hit(hit, req, ctx)
        else:
            response = await self.router.complete(req, ctx)
            # Clients asked for the alias; don't leak the upstream model name.
            response.model = req.model
            ctx.usage = response.usage
            await self._store(req, response, ctx)
        await self._settle(ctx)
        return response

    async def stream(
        self, req: ChatCompletionRequest, ctx: RequestContext
    ) -> AsyncIterator[ChatCompletionChunk]:
        if (hit := await self._lookup(req, ctx)) is not None:
            async for chunk in replay_as_chunks(self._serve_hit(hit, req, ctx)):
                self._mark_first_token(chunk, ctx)
                yield chunk
            await self._settle(ctx)
            return

        parts: list[str] = []
        usage: Usage | None = None
        finish: FinishReason | None = None
        async for chunk in self.router.stream(req, ctx):
            chunk.model = req.model
            self._mark_first_token(chunk, ctx)
            for choice in chunk.choices:
                parts.append(choice.delta.content or "")
                finish = choice.finish_reason or finish
            usage = chunk.usage or usage
            yield chunk

        # Reached only when the stream finished; a disconnect or error never caches a partial.
        ctx.usage = usage
        await self._settle(ctx)
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

    @staticmethod
    def _mark_first_token(chunk: ChatCompletionChunk, ctx: RequestContext) -> None:
        if ctx.ttft_ms is None and any(c.delta.content for c in chunk.choices):
            ctx.ttft_ms = ctx.elapsed_ms()

    async def aclose(self) -> None:
        for provider in self.providers.values():
            await provider.aclose()
