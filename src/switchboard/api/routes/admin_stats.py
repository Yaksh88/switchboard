"""Read-only stats for the dashboard. Response shapes are a contract with ``dashboard/``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import and_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from switchboard.auth.dependencies import require_admin
from switchboard.db.models import RequestLog
from switchboard.errors import AdminUnavailableError, NotFoundError

router = APIRouter(prefix="/admin/api", tags=["admin"], dependencies=[Depends(require_admin)])

Window = Literal["1h", "24h", "7d"]
# window -> (lookback, series bucket width)
WINDOWS: dict[str, tuple[timedelta, timedelta]] = {
    "1h": (timedelta(hours=1), timedelta(minutes=1)),
    "24h": (timedelta(hours=24), timedelta(minutes=15)),
    "7d": (timedelta(days=7), timedelta(hours=3)),
}
BUCKET_ORIGIN = datetime(2000, 1, 1, tzinfo=UTC)
# SQL fragments shared by the queries below. They are constants, never user input.
P50 = "coalesce(percentile_cont(0.5) within group (order by latency_ms), 0)"
P95 = "coalesce(percentile_cont(0.95) within group (order by latency_ms), 0)"
ERRORS = "count(*) filter (where status_code >= 400)"
CACHE_HITS = "count(*) filter (where cache in ('exact', 'semantic'))"


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] | None = request.app.state.sessions
    if sessions is None:
        raise AdminUnavailableError("Stats need a database: set SWITCHBOARD_DATABASE_URL")
    return sessions


def _rate(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _floor(ts: datetime, width: timedelta) -> datetime:
    """Round down to a bucket boundary, matching Postgres ``date_bin`` with BUCKET_ORIGIN."""
    return ts - (ts - BUCKET_ORIGIN) % width


@router.get("/overview")
async def overview(request: Request, window: Window = "1h") -> dict[str, Any]:
    lookback, width = WINDOWS[window]
    now = datetime.now(UTC)
    since = now - lookback
    async with _sessions(request)() as session:
        totals = (
            (
                await session.execute(
                    text(
                        f"""
                        select count(*) as requests,
                               {ERRORS} as errors,
                               {P50} as p50,
                               {P95} as p95,
                               coalesce(sum(cost_usd), 0) as cost,
                               coalesce(sum(prompt_tokens), 0) as prompt_tokens,
                               coalesce(sum(completion_tokens), 0) as completion_tokens,
                               {CACHE_HITS} as cache_hits
                        from request_logs where created_at >= :since
                        """
                    ),
                    {"since": since},
                )
            )
            .mappings()
            .one()
        )
        rows = (
            (
                await session.execute(
                    text(
                        f"""
                        select date_bin(cast(:width as interval), created_at, :origin) as ts,
                               count(*) as requests,
                               {ERRORS} as errors,
                               {P95} as p95,
                               coalesce(sum(cost_usd), 0) as cost,
                               {CACHE_HITS} as cache_hits
                        from request_logs where created_at >= :since
                        group by 1 order by 1
                        """
                    ),
                    {"since": since, "width": width, "origin": BUCKET_ORIGIN},
                )
            )
            .mappings()
            .all()
        )

    by_ts = {r["ts"]: r for r in rows}
    series = []
    ts = _floor(since, width)
    while ts <= now:
        row = by_ts.get(ts)
        series.append(
            {
                "ts": ts.isoformat(),
                "requests": row["requests"] if row else 0,
                "errors": row["errors"] if row else 0,
                "p95_ms": float(row["p95"]) if row else 0.0,
                "cost_usd": float(row["cost"]) if row else 0.0,
                "cache_hits": row["cache_hits"] if row else 0,
            }
        )
        ts += width

    requests = int(totals["requests"])
    return {
        "window": window,
        "totals": {
            "requests": requests,
            "errors": int(totals["errors"]),
            "error_rate": _rate(int(totals["errors"]), requests),
            "p50_ms": float(totals["p50"]),
            "p95_ms": float(totals["p95"]),
            "cost_usd": float(totals["cost"]),
            "prompt_tokens": int(totals["prompt_tokens"]),
            "completion_tokens": int(totals["completion_tokens"]),
            "cache_hit_rate": _rate(int(totals["cache_hits"]), requests),
        },
        "series": series,
    }


@router.get("/models")
async def models(request: Request, window: Window = "24h") -> list[dict[str, Any]]:
    since = datetime.now(UTC) - WINDOWS[window][0]
    async with _sessions(request)() as session:
        rows = (
            (
                await session.execute(
                    text(
                        f"""
                        select model_alias as model,
                               count(*) as requests,
                               {ERRORS} as errors,
                               {P50} as p50,
                               {P95} as p95,
                               coalesce(sum(cost_usd), 0) as cost,
                               {CACHE_HITS} as cache_hits
                        from request_logs where created_at >= :since
                        group by model_alias order by requests desc, model_alias
                        """
                    ),
                    {"since": since},
                )
            )
            .mappings()
            .all()
        )
    return [
        {
            "model": r["model"],
            "requests": r["requests"],
            "error_rate": _rate(r["errors"], r["requests"]),
            "p50_ms": float(r["p50"]),
            "p95_ms": float(r["p95"]),
            "cost_usd": float(r["cost"]),
            "cache_hit_rate": _rate(r["cache_hits"], r["requests"]),
        }
        for r in rows
    ]


class RequestLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: str
    created_at: datetime
    api_key_id: uuid.UUID | None
    model_alias: str
    provider: str | None
    upstream_model: str | None
    status_code: int
    cache: str
    attempts: int
    stream: bool
    latency_ms: float
    ttft_ms: float | None
    prompt_tokens: int
    completion_tokens: int
    cost_usd: Decimal
    error: str | None


class RequestPage(BaseModel):
    items: list[RequestLogOut]
    next_before_id: int | None


@router.get("/requests")
async def list_requests(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    before_id: int | None = None,
    model: str | None = None,
    status: Literal["ok", "error"] | None = None,
    cache: Literal["miss", "exact", "semantic", "bypass"] | None = None,
) -> RequestPage:
    conditions = []
    if before_id is not None:
        conditions.append(RequestLog.id < before_id)
    if model:
        conditions.append(RequestLog.model_alias == model)
    if status == "ok":
        conditions.append(RequestLog.status_code < 400)
    elif status == "error":
        conditions.append(RequestLog.status_code >= 400)
    if cache:
        conditions.append(RequestLog.cache == cache)

    query = select(RequestLog).order_by(RequestLog.id.desc()).limit(limit + 1)
    if conditions:
        query = query.where(and_(*conditions))
    async with _sessions(request)() as session:
        rows = list(await session.scalars(query))

    page, more = rows[:limit], len(rows) > limit
    return RequestPage(
        items=[RequestLogOut.model_validate(r) for r in page],
        next_before_id=page[-1].id if more else None,
    )


@router.get("/requests/{request_id}")
async def get_request(request_id: str, request: Request) -> RequestLogOut:
    async with _sessions(request)() as session:
        row = await session.scalar(select(RequestLog).where(RequestLog.request_id == request_id))
    if row is None:
        raise NotFoundError(f"No request with id {request_id}")
    return RequestLogOut.model_validate(row)


@router.get("/providers")
async def providers(request: Request) -> list[dict[str, str]]:
    health: list[dict[str, str]] = request.app.state.gateway.health()
    return health
