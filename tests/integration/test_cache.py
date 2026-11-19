from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis as SyncRedis
from redis.asyncio import Redis

from switchboard.cache.embeddings import HashingEmbedder
from switchboard.cache.exact import ExactCache
from switchboard.cache.keys import exact_key, semantic_namespace
from switchboard.cache.semantic import SemanticCache
from switchboard.config import Settings
from switchboard.schemas import (
    AssistantMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
)

pytestmark = pytest.mark.integration

ClientFactory = Callable[..., TestClient]


def make_req(text: str, system: str = "be brief") -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(
        {
            "model": "m",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
        }
    )


def make_resp(text: str) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        model="m", choices=[Choice(message=AssistantMessage(content=text))]
    )


def chat(client: TestClient, text: str, **extra: Any) -> Any:
    headers = extra.pop("headers", {})
    body = {"model": "mock", "messages": [{"role": "user", "content": text}], **extra}
    return client.post("/v1/chat/completions", json=body, headers=headers)


def cached_client(make_client: ClientFactory, redis_url: str, **cache: Any) -> TestClient:
    return make_client(Settings(redis_url=redis_url), cache={"enabled": True, **cache})


async def test_exact_cache_round_trip_and_ttl(redis_client: Redis) -> None:
    cache = ExactCache(redis_client, ttl_s=60)
    req = make_req("hello")
    assert await cache.get(req) is None

    await cache.set(req, make_resp("world"))
    hit = await cache.get(req)
    assert hit is not None and hit.text() == "world"
    assert 0 < await redis_client.ttl(exact_key(req)) <= 60


async def test_semantic_cache_hits_paraphrase_above_threshold(redis_client: Redis) -> None:
    cache = SemanticCache(redis_client, HashingEmbedder(), threshold=0.7, max_entries=10, ttl_s=60)
    await cache.set(make_req("how do I reset my password"), make_resp("use the reset link"))

    hit = await cache.get(make_req("how can I reset my password"))
    assert hit is not None
    response, similarity = hit
    assert response.text() == "use the reset link"
    assert similarity >= 0.7


async def test_semantic_cache_misses_unrelated_prompt(redis_client: Redis) -> None:
    cache = SemanticCache(redis_client, HashingEmbedder(), threshold=0.7, max_entries=10, ttl_s=60)
    await cache.set(make_req("how do I reset my password"), make_resp("use the reset link"))
    assert await cache.get(make_req("what is the capital of France")) is None


async def test_semantic_cache_is_scoped_to_the_conversation_context(redis_client: Redis) -> None:
    cache = SemanticCache(redis_client, HashingEmbedder(), threshold=0.7, max_entries=10, ttl_s=60)
    await cache.set(make_req("reset my password", system="support bot"), make_resp("A"))
    assert await cache.get(make_req("reset my password", system="pirate")) is None


async def test_semantic_cache_evicts_oldest_beyond_max_entries(redis_client: Redis) -> None:
    cache = SemanticCache(redis_client, HashingEmbedder(), threshold=0.9, max_entries=3, ttl_s=60)
    prompts = [
        "apples and oranges",
        "quantum entanglement basics",
        "python asyncio tutorial",
        "best pizza in naples",
    ]
    for prompt in prompts:
        await cache.set(make_req(prompt), make_resp(prompt))

    assert await redis_client.hlen(semantic_namespace(make_req("x"))) == 3
    assert await cache.get(make_req(prompts[0])) is None
    assert await cache.get(make_req(prompts[3])) is not None


def test_gateway_serves_second_identical_request_from_cache(
    make_client: ClientFactory, redis_url: str, sync_redis: SyncRedis
) -> None:
    client = cached_client(make_client, redis_url)

    first = chat(client, "ping")
    assert first.headers["x-switchboard-cache"] == "miss"
    assert first.headers["x-switchboard-provider"] == "mock"

    second = chat(client, "ping")
    assert second.headers["x-switchboard-cache"] == "exact"
    assert second.headers["x-switchboard-provider"] == "cache"
    assert second.json()["choices"] == first.json()["choices"]
    assert second.json()["model"] == "mock"


def test_streamed_response_populates_cache_and_cached_stream_replays(
    make_client: ClientFactory, redis_url: str, sync_redis: SyncRedis
) -> None:
    client = cached_client(make_client, redis_url)

    def stream_text() -> str:
        text = ""
        with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": "mock",
                "stream": True,
                "messages": [{"role": "user", "content": "a b"}],
            },
        ) as resp:
            for line in resp.iter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    for choice in json.loads(line[6:]).get("choices", []):
                        text += choice["delta"].get("content") or ""
        return text

    assert stream_text().strip() == "echo: a b"  # miss: stored once the stream completes
    follow_up = chat(client, "a b")
    assert follow_up.headers["x-switchboard-cache"] == "exact"
    assert stream_text().strip() == "echo: a b"  # hit: replayed from cache


def test_semantic_hit_through_the_gateway(
    make_client: ClientFactory, redis_url: str, sync_redis: SyncRedis
) -> None:
    client = cached_client(make_client, redis_url, semantic={"enabled": True, "threshold": 0.7})
    chat(client, "how do I reset my password")
    resp = chat(client, "how can I reset my password")
    assert resp.headers["x-switchboard-cache"] == "semantic"
    assert resp.json()["choices"][0]["message"]["content"] == "echo: how do I reset my password"


def test_nonzero_temperature_requests_are_not_cached(
    make_client: ClientFactory, redis_url: str, sync_redis: SyncRedis
) -> None:
    client = cached_client(make_client, redis_url)
    for _ in range(2):
        assert chat(client, "ping", temperature=0.7).headers["x-switchboard-cache"] == "bypass"
    assert sync_redis.keys("sb:cache:*") == []


def test_errors_are_not_cached(
    make_client: ClientFactory, redis_url: str, sync_redis: SyncRedis
) -> None:
    client = make_client(
        Settings(redis_url=redis_url),
        providers={"mock": {"type": "mock", "failure_rate": 1.0, "max_retries": 0}},
        cache={"enabled": True},
    )
    assert chat(client, "ping").status_code == 503
    assert chat(client, "ping").status_code == 503
    assert sync_redis.keys("sb:cache:*") == []


def test_bypass_header_skips_cache(
    make_client: ClientFactory, redis_url: str, sync_redis: SyncRedis
) -> None:
    client = cached_client(make_client, redis_url)
    for _ in range(2):
        resp = chat(client, "ping", headers={"x-switchboard-cache": "bypass"})
        assert resp.headers["x-switchboard-cache"] == "bypass"
        assert resp.headers["x-switchboard-provider"] == "mock"
    assert sync_redis.keys("sb:cache:*") == []


def test_redis_outage_degrades_to_uncached_requests(make_client: ClientFactory) -> None:
    client = make_client(Settings(redis_url="redis://localhost:1/0"), cache={"enabled": True})
    resp = chat(client, "ping")
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "echo: ping"
