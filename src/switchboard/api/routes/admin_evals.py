from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from switchboard.auth.dependencies import require_admin
from switchboard.db.models import EvalRun
from switchboard.errors import AdminUnavailableError, NotFoundError

router = APIRouter(prefix="/admin/api/evals", tags=["admin"], dependencies=[Depends(require_admin)])


class ScoreIn(BaseModel):
    type: str
    passed: bool
    score: float
    reason: str


class CaseIn(BaseModel):
    case_id: str
    passed: bool
    critical: bool
    output: str
    latency_ms: float
    cost_usd: float | None
    error: str | None
    scores: list[ScoreIn]


class EvalRunIn(BaseModel):
    """The body ``RunResult.to_json()`` produces."""

    suite: str = Field(min_length=1, max_length=200)
    model: str = Field(min_length=1, max_length=100)
    started_at: datetime
    duration_s: float = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    total_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    cases: list[CaseIn]


class EvalRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    suite: str
    model: str
    created_at: datetime
    pass_rate: float
    duration_s: float
    total_cases: int
    passed_cases: int


class EvalRunDetail(EvalRunSummary):
    result: dict[str, Any]


def _sessions(request: Request) -> async_sessionmaker[AsyncSession]:
    sessions: async_sessionmaker[AsyncSession] | None = request.app.state.sessions
    if sessions is None:
        raise AdminUnavailableError("Eval storage needs a database: set SWITCHBOARD_DATABASE_URL")
    return sessions


@router.post("", status_code=201)
async def store_run(body: EvalRunIn, request: Request) -> EvalRunSummary:
    run = EvalRun(
        suite=body.suite,
        model=body.model,
        created_at=body.started_at,
        pass_rate=body.pass_rate,
        duration_s=body.duration_s,
        total_cases=body.total_cases,
        passed_cases=body.passed_cases,
        result=body.model_dump(mode="json"),
    )
    async with _sessions(request)() as session:
        session.add(run)
        await session.commit()
    return EvalRunSummary.model_validate(run)


@router.get("")
async def list_runs(
    request: Request, suite: str | None = None, limit: int = Query(50, ge=1, le=500)
) -> list[EvalRunSummary]:
    query = select(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit)
    if suite:
        query = query.where(EvalRun.suite == suite)
    async with _sessions(request)() as session:
        return [EvalRunSummary.model_validate(r) for r in await session.scalars(query)]


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, request: Request) -> EvalRunDetail:
    async with _sessions(request)() as session:
        run = await session.get(EvalRun, run_id)
    if run is None:
        raise NotFoundError(f"No eval run with id {run_id}")
    return EvalRunDetail.model_validate(run)
