from __future__ import annotations

from redis.asyncio import Redis


def create_redis(url: str) -> Redis:
    # Cached payloads and vectors are binary, so keep responses as bytes.
    return Redis.from_url(url, decode_responses=False)
