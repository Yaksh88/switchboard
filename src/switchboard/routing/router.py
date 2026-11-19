"""Picks an upstream target for a request and fails over between targets."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable

from switchboard.config import GatewayConfig, Target
from switchboard.context import RequestContext
from switchboard.errors import ModelNotFoundError, ProviderError
from switchboard.providers import Provider
from switchboard.routing.circuit_breaker import CircuitBreaker
from switchboard.schemas import ChatCompletionChunk, ChatCompletionRequest, ChatCompletionResponse

BACKOFF_BASE_S = 0.25
BACKOFF_JITTER_S = 0.1


def backoff_delay(retry: int) -> float:
    return float(BACKOFF_BASE_S * 2**retry + random.uniform(0, BACKOFF_JITTER_S))


class Router:
    """Walks a model alias's targets in order.

    Each target gets ``1 + max_retries`` attempts on retryable errors, with
    exponential backoff between them. Non-retryable errors (e.g. a 400) are raised
    immediately because every other target would reject the same request. Targets
    whose circuit breaker is open are skipped.
    """

    def __init__(
        self,
        config: GatewayConfig,
        providers: dict[str, Provider],
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.providers = providers
        self._sleep = sleep
        self.breakers: dict[tuple[str, str], CircuitBreaker] = {
            (t.provider, t.model): CircuitBreaker()
            for model in config.models.values()
            for t in model.targets
        }

    def _targets(self, alias: str) -> list[Target]:
        model = self.config.models.get(alias)
        if model is None:
            raise ModelNotFoundError(alias)
        return model.targets

    @staticmethod
    def _no_healthy_targets(alias: str) -> ProviderError:
        return ProviderError(
            "router", f"no healthy targets for '{alias}'", status_code=503, retryable=False
        )

    @staticmethod
    def _begin(ctx: RequestContext, target: Target) -> None:
        ctx.attempts += 1
        ctx.provider = target.provider
        ctx.upstream_model = target.model

    async def complete(
        self, req: ChatCompletionRequest, ctx: RequestContext
    ) -> ChatCompletionResponse:
        last_error: ProviderError | None = None
        for target in self._targets(req.model):
            provider = self.providers[target.provider]
            breaker = self.breakers[(target.provider, target.model)]
            for retry in range(provider.config.max_retries + 1):
                if not breaker.allow():
                    break
                self._begin(ctx, target)
                try:
                    response = await provider.complete(req, target.model)
                except ProviderError as exc:
                    if not exc.retryable:
                        raise
                    breaker.record_failure()
                    last_error = exc
                    if retry < provider.config.max_retries:
                        await self._sleep(backoff_delay(retry))
                    continue
                breaker.record_success()
                return response
        raise last_error or self._no_healthy_targets(req.model)

    async def stream(
        self, req: ChatCompletionRequest, ctx: RequestContext
    ) -> AsyncIterator[ChatCompletionChunk]:
        last_error: ProviderError | None = None
        for target in self._targets(req.model):
            provider = self.providers[target.provider]
            breaker = self.breakers[(target.provider, target.model)]
            for retry in range(provider.config.max_retries + 1):
                if not breaker.allow():
                    break
                self._begin(ctx, target)
                started = False
                try:
                    async for chunk in provider.stream(req, target.model):
                        started = True
                        yield chunk
                except ProviderError as exc:
                    # Once text has reached the client, retrying elsewhere would duplicate it.
                    if started or not exc.retryable:
                        if started:
                            breaker.record_failure()
                        raise
                    breaker.record_failure()
                    last_error = exc
                    if retry < provider.config.max_retries:
                        await self._sleep(backoff_delay(retry))
                    continue
                breaker.record_success()
                return
        raise last_error or self._no_healthy_targets(req.model)

    def health(self) -> list[dict[str, str]]:
        return [
            {"provider": provider, "model": model, "state": breaker.state.value}
            for (provider, model), breaker in self.breakers.items()
        ]
