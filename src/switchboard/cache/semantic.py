from __future__ import annotations

import base64
import json
import time
import uuid

import numpy as np
from redis.asyncio import Redis

from switchboard.cache.embeddings import Embedder
from switchboard.cache.keys import last_user_text, semantic_namespace
from switchboard.schemas import ChatCompletionRequest, ChatCompletionResponse


class SemanticCache:
    """Embedding-similarity match on the final user message.

    Entries live in a Redis hash per namespace (model + conversation context), with a
    sorted set recording insertion order so the oldest entries can be evicted. A lookup
    loads the namespace's vectors and scores them against the query in one matmul, which
    is fine for the few-thousand-entry namespaces ``max_entries`` allows.
    """

    def __init__(
        self,
        redis: Redis,
        embedder: Embedder,
        threshold: float,
        max_entries: int,
        ttl_s: int,
    ) -> None:
        self.redis = redis
        self.embedder = embedder
        self.threshold = threshold
        self.max_entries = max_entries
        self.ttl_s = ttl_s

    async def get(self, req: ChatCompletionRequest) -> tuple[ChatCompletionResponse, float] | None:
        entries = await self.redis.hgetall(semantic_namespace(req))
        if not entries:
            return None
        query = await self.embedder.embed(last_user_text(req))

        vectors: list[np.ndarray] = []
        responses: list[dict[str, object]] = []
        for raw in entries.values():
            entry = json.loads(raw)
            vector = np.frombuffer(base64.b64decode(entry["v"]), dtype=np.float32)
            if vector.shape == query.shape:  # skip entries written by a different embedder
                vectors.append(vector)
                responses.append(entry["r"])
        if not vectors:
            return None

        scores = np.stack(vectors) @ query
        best = int(np.argmax(scores))
        if float(scores[best]) < self.threshold:
            return None
        return ChatCompletionResponse.model_validate(responses[best]), float(scores[best])

    async def set(self, req: ChatCompletionRequest, resp: ChatCompletionResponse) -> None:
        namespace = semantic_namespace(req)
        order_key = f"{namespace}:order"
        vector = await self.embedder.embed(last_user_text(req))
        entry_id = uuid.uuid4().hex
        payload = json.dumps(
            {
                "v": base64.b64encode(vector.astype(np.float32).tobytes()).decode(),
                "r": resp.model_dump(mode="json"),
            }
        )

        pipe = self.redis.pipeline(transaction=True)
        pipe.hset(namespace, entry_id, payload)
        pipe.zadd(order_key, {entry_id: time.time()})
        pipe.expire(namespace, self.ttl_s)
        pipe.expire(order_key, self.ttl_s)
        pipe.zcard(order_key)
        count = int((await pipe.execute())[-1])

        overflow = count - self.max_entries
        if overflow > 0:
            oldest = await self.redis.zrange(order_key, 0, overflow - 1)
            if oldest:
                evict = self.redis.pipeline(transaction=True)
                evict.hdel(namespace, *oldest)
                evict.zrem(order_key, *oldest)
                await evict.execute()
