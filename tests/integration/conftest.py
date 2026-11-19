from __future__ import annotations

from collections.abc import AsyncIterator, Iterator

import pytest
import redis as sync_redis_lib
from redis.asyncio import Redis
from redis.exceptions import RedisError

from switchboard.redis import create_redis

REDIS_URL = "redis://localhost:6379/15"


@pytest.fixture
async def redis_client() -> AsyncIterator[Redis]:
    client = create_redis(REDIS_URL)
    try:
        await client.ping()
    except (RedisError, OSError):
        await client.aclose()
        pytest.skip("redis unavailable")
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
def sync_redis() -> Iterator[sync_redis_lib.Redis]:
    """Synchronous handle for assertions made from tests that drive a TestClient."""
    client = sync_redis_lib.Redis.from_url(REDIS_URL)
    try:
        client.ping()
    except (RedisError, OSError):
        pytest.skip("redis unavailable")
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
def redis_url() -> str:
    return REDIS_URL
