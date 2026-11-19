from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from redis.asyncio import Redis

_SCRIPT = (Path(__file__).with_name("token_bucket.lua")).read_text()


@dataclass(frozen=True)
class Decision:
    allowed: bool
    remaining: int
    retry_after_s: float


class RateLimiter:
    """Token buckets in Redis. The refill-and-take step is one Lua script, so concurrent
    requests (even across gateway replicas) can't both take the last token."""

    def __init__(self, redis: Redis, clock: Callable[[], float] = time.time) -> None:
        self._script = redis.register_script(_SCRIPT)
        self._clock = clock

    async def _run(
        self, key: str, cost: int, capacity: int, refill_per_s: float, *, allow_negative: bool
    ) -> Decision:
        allowed, remaining, retry = await self._script(
            keys=[key],
            args=[capacity, refill_per_s, cost, int(self._clock() * 1000), int(allow_negative)],
        )
        return Decision(bool(int(allowed)), int(remaining), float(retry))

    async def consume(self, key: str, cost: int, capacity: int, refill_per_s: float) -> Decision:
        """Take ``cost`` tokens if available; otherwise leave the bucket untouched."""
        return await self._run(key, cost, capacity, refill_per_s, allow_negative=False)

    async def debit(self, key: str, cost: int, capacity: int, refill_per_s: float) -> None:
        """Take ``cost`` tokens unconditionally; the bucket may go negative."""
        await self._run(key, cost, capacity, refill_per_s, allow_negative=True)
