from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from switchboard.config import GatewayConfig, ProviderConfig
from switchboard.context import RequestContext
from switchboard.errors import ProviderError
from switchboard.providers.base import Provider
from switchboard.routing.router import Router
from switchboard.schemas import (
    AssistantMessage,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChunkChoice,
    Delta,
)


def ok(text: str = "ok") -> ChatCompletionResponse:
    return ChatCompletionResponse(
        model="x", choices=[Choice(message=AssistantMessage(content=text))]
    )


def fail(status: int = 503) -> ProviderError:
    return ProviderError("p", "boom", status_code=status)


def chunk(text: str) -> ChatCompletionChunk:
    return ChatCompletionChunk(id="c", model="x", choices=[ChunkChoice(delta=Delta(content=text))])


class ScriptedProvider(Provider):
    """Pops the next scripted outcome (a response/chunk list or an exception) per call."""

    def __init__(self, name: str, script: list[object], max_retries: int = 1) -> None:
        super().__init__(name, ProviderConfig(type="mock", max_retries=max_retries))
        self.script = list(script)
        self.calls = 0

    def _next(self) -> object:
        self.calls += 1
        return self.script.pop(0)

    async def complete(self, req: ChatCompletionRequest, model: str) -> ChatCompletionResponse:
        outcome = self._next()
        if isinstance(outcome, Exception):
            raise outcome
        assert isinstance(outcome, ChatCompletionResponse)
        return outcome

    async def stream(
        self, req: ChatCompletionRequest, model: str
    ) -> AsyncIterator[ChatCompletionChunk]:
        outcome = self._next()
        assert isinstance(outcome, list)
        for item in outcome:
            if isinstance(item, Exception):
                raise item
            yield item


def build(a: ScriptedProvider, b: ScriptedProvider | None = None) -> tuple[Router, AsyncMock]:
    providers: dict[str, Provider] = {a.name: a}
    targets = [{"provider": a.name, "model": "ma"}]
    if b is not None:
        providers[b.name] = b
        targets.append({"provider": b.name, "model": "mb"})
    config = GatewayConfig.model_validate(
        {
            "providers": {name: {"type": "mock"} for name in providers},
            "models": {"alias": {"targets": targets}},
        }
    )
    sleep = AsyncMock()
    return Router(config, providers, sleep=sleep), sleep


REQ = ChatCompletionRequest.model_validate(
    {"model": "alias", "messages": [{"role": "user", "content": "hi"}]}
)


def ctx() -> RequestContext:
    return RequestContext(model_alias="alias")


async def test_falls_back_to_next_target_on_retryable_error() -> None:
    a = ScriptedProvider("a", [fail(), fail()])
    b = ScriptedProvider("b", [ok("from b")])
    router, _ = build(a, b)
    c = ctx()
    resp = await router.complete(REQ, c)
    assert resp.text() == "from b"
    assert c.provider == "b"
    assert c.upstream_model == "mb"
    assert c.attempts == 3


async def test_non_retryable_error_is_raised_without_fallback() -> None:
    a = ScriptedProvider("a", [fail(400)])
    b = ScriptedProvider("b", [ok()])
    router, _ = build(a, b)
    with pytest.raises(ProviderError) as info:
        await router.complete(REQ, ctx())
    assert info.value.status_code == 400
    assert b.calls == 0


async def test_retries_use_exponential_backoff() -> None:
    a = ScriptedProvider("a", [fail(), ok()], max_retries=2)
    router, sleep = build(a)
    await router.complete(REQ, ctx())
    sleep.assert_awaited_once()
    assert 0.25 <= sleep.await_args.args[0] <= 0.35


async def test_open_breaker_target_is_skipped() -> None:
    a = ScriptedProvider("a", [ok("from a")])
    b = ScriptedProvider("b", [ok("from b")])
    router, _ = build(a, b)
    breaker = router.breakers[("a", "ma")]
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    resp = await router.complete(REQ, ctx())
    assert resp.text() == "from b"
    assert a.calls == 0


async def test_all_breakers_open_raises_503_no_healthy_targets() -> None:
    a = ScriptedProvider("a", [])
    router, _ = build(a)
    breaker = router.breakers[("a", "ma")]
    for _ in range(breaker.failure_threshold):
        breaker.record_failure()
    with pytest.raises(ProviderError) as info:
        await router.complete(REQ, ctx())
    assert info.value.status_code == 503
    assert "no healthy targets" in info.value.message


async def test_stream_falls_back_before_first_chunk() -> None:
    a = ScriptedProvider("a", [[fail()], [fail()]])
    b = ScriptedProvider("b", [[chunk("x"), chunk("y")]])
    router, _ = build(a, b)
    c = ctx()
    chunks = [ch async for ch in router.stream(REQ, c)]
    assert [ch.choices[0].delta.content for ch in chunks] == ["x", "y"]
    assert c.provider == "b"


async def test_stream_does_not_fall_back_after_first_chunk() -> None:
    a = ScriptedProvider("a", [[chunk("x"), fail()]])
    b = ScriptedProvider("b", [[chunk("never")]])
    router, _ = build(a, b)
    received: list[str | None] = []
    with pytest.raises(ProviderError):
        async for ch in router.stream(REQ, ctx()):
            received.append(ch.choices[0].delta.content)
    assert received == ["x"]
    assert b.calls == 0


def test_health_reports_every_target() -> None:
    router, _ = build(ScriptedProvider("a", []), ScriptedProvider("b", []))
    assert router.health() == [
        {"provider": "a", "model": "ma", "state": "closed"},
        {"provider": "b", "model": "mb", "state": "closed"},
    ]
