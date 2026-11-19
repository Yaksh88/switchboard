from __future__ import annotations

from redis.asyncio import Redis

from switchboard.cache.keys import exact_key
from switchboard.schemas import ChatCompletionRequest, ChatCompletionResponse


class ExactCache:
    """Byte-for-byte request match: same model, messages and sampling params."""

    def __init__(self, redis: Redis, ttl_s: int) -> None:
        self.redis = redis
        self.ttl_s = ttl_s

    async def get(self, req: ChatCompletionRequest) -> ChatCompletionResponse | None:
        raw = await self.redis.get(exact_key(req))
        return None if raw is None else ChatCompletionResponse.model_validate_json(raw)

    async def set(self, req: ChatCompletionRequest, resp: ChatCompletionResponse) -> None:
        await self.redis.set(exact_key(req), resp.model_dump_json(), ex=self.ttl_s)
