from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from switchboard.context import RequestContext
from switchboard.db.models import RequestLog
from switchboard.observability.metrics import LOG_DROPPED

log = logging.getLogger("switchboard.log_writer")

MAX_ERROR_CHARS = 1000


def to_row(ctx: RequestContext) -> dict[str, Any]:
    usage = ctx.usage
    return {
        "request_id": ctx.request_id,
        "created_at": ctx.created_at,
        "api_key_id": ctx.api_key_id,
        "model_alias": ctx.model_alias,
        "provider": ctx.provider,
        "upstream_model": ctx.upstream_model,
        "status_code": ctx.status_code,
        "cache": ctx.cache,
        "attempts": ctx.attempts,
        "stream": ctx.stream,
        "latency_ms": ctx.elapsed_ms(),
        "ttft_ms": ctx.ttft_ms,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
        "cost_usd": ctx.cost_usd,
        "error": ctx.error[:MAX_ERROR_CHARS] if ctx.error else None,
    }


class LogWriter:
    """Writes request logs to Postgres off the request path.

    ``submit`` never blocks: rows go onto a bounded queue and a background task inserts
    them in batches. If the queue is full the row is dropped (and counted) rather than
    slowing requests down; ``close`` drains whatever is queued before returning.
    """

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        max_queue: int = 10_000,
        batch_size: int = 200,
        flush_interval_s: float = 1.0,
    ) -> None:
        self.sessions = sessions
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        # ``None`` is a wake-up sentinel used by ``close``.
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=max_queue)
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    def submit(self, ctx: RequestContext) -> None:
        # Snapshot now: latency must be measured at completion, not when the row is flushed.
        try:
            self._queue.put_nowait(to_row(ctx))
        except asyncio.QueueFull:
            LOG_DROPPED.inc()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="switchboard-log-writer")

    async def close(self) -> None:
        self._stopping = True
        # If the queue is full the loop is already busy draining it and will see ``_stopping``.
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        if self._task is not None:
            await self._task
        else:
            await self._drain()

    async def _drain(self) -> None:
        while not self._queue.empty():
            batch = self._take_batch([])
            if batch:
                await self._flush(batch)

    def _take_batch(self, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        while len(batch) < self.batch_size and not self._queue.empty():
            row = self._queue.get_nowait()
            if row is not None:
                batch.append(row)
        return batch

    async def _run(self) -> None:
        while True:
            batch: list[dict[str, Any]] = []
            try:
                first = await asyncio.wait_for(self._queue.get(), self.flush_interval_s)
                if first is not None:
                    batch.append(first)
            except TimeoutError:
                pass
            self._take_batch(batch)
            if batch:
                await self._flush(batch)
            if self._stopping and self._queue.empty():
                return

    async def _flush(self, rows: list[dict[str, Any]]) -> None:
        try:
            async with self.sessions() as session:
                await session.execute(insert(RequestLog), rows)
                await session.commit()
        except Exception:
            # Logging must never take the gateway down; account for the loss and move on.
            log.exception("failed to write %d request log rows", len(rows))
            LOG_DROPPED.inc(len(rows))
