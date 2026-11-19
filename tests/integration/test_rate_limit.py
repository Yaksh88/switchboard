from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis as SyncRedis
from redis.asyncio import Redis

from switchboard.config import Settings
from switchboard.ratelimit.limiter import RateLimiter

pytestmark = pytest.mark.integration

ADMIN = {"authorization": "Bearer test-admin"}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


async def test_bucket_allows_capacity_then_rejects_with_retry_after(redis_client: Redis) -> None:
    limiter = RateLimiter(redis_client, clock=Clock())
    results = [await limiter.consume("b", 1, capacity=3, refill_per_s=1) for _ in range(4)]

    assert [r.allowed for r in results] == [True, True, True, False]
    assert [r.remaining for r in results[:3]] == [2, 1, 0]
    assert 0 < results[3].retry_after_s <= 1


async def test_bucket_refills_over_time(redis_client: Redis) -> None:
    clock = Clock()
    limiter = RateLimiter(redis_client, clock=clock)
    for _ in range(3):
        await limiter.consume("b", 1, capacity=3, refill_per_s=1)
    assert not (await limiter.consume("b", 1, capacity=3, refill_per_s=1)).allowed

    clock.now += 2
    assert (await limiter.consume("b", 2, capacity=3, refill_per_s=1)).allowed
    assert not (await limiter.consume("b", 1, capacity=3, refill_per_s=1)).allowed


async def test_refill_never_exceeds_capacity(redis_client: Redis) -> None:
    clock = Clock()
    limiter = RateLimiter(redis_client, clock=clock)
    await limiter.consume("b", 1, capacity=3, refill_per_s=1)
    clock.now += 3600
    assert (await limiter.consume("b", 1, capacity=3, refill_per_s=1)).remaining == 2


async def test_debit_can_drive_the_bucket_negative(redis_client: Redis) -> None:
    limiter = RateLimiter(redis_client, clock=Clock())
    await limiter.debit("b", 50, capacity=10, refill_per_s=1)
    decision = await limiter.consume("b", 1, capacity=10, refill_per_s=1)
    assert not decision.allowed
    assert decision.retry_after_s > 40


async def test_token_bucket_is_atomic_under_concurrency(redis_client: Redis) -> None:
    limiter = RateLimiter(redis_client, clock=Clock())
    decisions = await asyncio.gather(
        *(limiter.consume("b", 1, capacity=10, refill_per_s=0) for _ in range(50))
    )
    assert sum(d.allowed for d in decisions) == 10


@pytest.fixture
def api(
    make_client: Callable[..., TestClient], clean_db: str, redis_url: str, sync_redis: SyncRedis
) -> TestClient:
    return make_client(
        Settings(
            database_url=clean_db, redis_url=redis_url, auth_enabled=True, admin_token="test-admin"
        )
    )


def key_with(client: TestClient, **limits: Any) -> str:
    resp = client.post("/admin/keys", json={"name": "k", **limits}, headers=ADMIN)
    return str(resp.json()["key"])


def chat(client: TestClient, key: str, content: str = "hi") -> Any:
    return client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [{"role": "user", "content": content}]},
        headers={"authorization": f"Bearer {key}"},
    )


def test_rpm_limit_returns_429_with_headers(api: TestClient) -> None:
    key = key_with(api, rpm_limit=2)

    first, second = chat(api, key), chat(api, key)
    assert first.status_code == second.status_code == 200
    assert first.headers["x-ratelimit-remaining-requests"] == "1"

    third = chat(api, key)
    assert third.status_code == 429
    assert int(third.headers["retry-after"]) >= 1
    assert third.headers["x-ratelimit-remaining-requests"] == "0"
    assert third.json()["error"]["code"] == "rate_limit_exceeded"


def test_limits_are_tracked_per_key(api: TestClient) -> None:
    limited, other = key_with(api, rpm_limit=1), key_with(api, rpm_limit=1)
    assert chat(api, limited).status_code == 200
    assert chat(api, limited).status_code == 429
    assert chat(api, other).status_code == 200


def test_tpm_limit_charges_prompt_tokens_up_front(api: TestClient) -> None:
    key = key_with(api, tpm_limit=100)
    big_prompt = "x" * 400  # ~100 estimated tokens: the whole minute's budget
    assert chat(api, key, big_prompt).status_code == 200

    resp = chat(api, key, big_prompt)
    assert resp.status_code == 429
    assert "tokens per minute" in resp.json()["error"]["message"]


def test_prompt_larger_than_the_whole_tpm_budget_is_still_admitted_once(api: TestClient) -> None:
    key = key_with(api, tpm_limit=10)
    assert chat(api, key, "x" * 4000).status_code == 200


def test_limiter_outage_admits_requests(
    make_client: Callable[..., TestClient], clean_db: str
) -> None:
    client = make_client(
        Settings(
            database_url=clean_db,
            redis_url="redis://localhost:1/0",
            auth_enabled=True,
            admin_token="test-admin",
        )
    )
    key = key_with(client, rpm_limit=1)
    assert chat(client, key).status_code == 200
    assert chat(client, key).status_code == 200
