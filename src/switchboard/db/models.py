from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ApiKey(Base):
    """A virtual key issued to a client. Only the sha256 of the secret is stored."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    key_prefix: Mapped[str] = mapped_column(String(12))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    rpm_limit: Mapped[int | None]
    tpm_limit: Mapped[int | None]
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    allowed_models: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RequestLog(Base):
    """One row per gateway request, written asynchronously in batches."""

    __tablename__ = "request_logs"
    __table_args__ = (Index("ix_request_logs_api_key_id_created_at", "api_key_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(32), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("api_keys.id", ondelete="SET NULL"), index=True
    )
    model_alias: Mapped[str] = mapped_column(String(100), index=True)
    provider: Mapped[str | None] = mapped_column(String(100))
    upstream_model: Mapped[str | None] = mapped_column(String(100))
    status_code: Mapped[int] = mapped_column(Integer)
    cache: Mapped[str] = mapped_column(String(10))
    attempts: Mapped[int] = mapped_column(Integer)
    stream: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[float] = mapped_column(Float)
    ttft_ms: Mapped[float | None] = mapped_column(Float)
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    error: Mapped[str | None] = mapped_column(Text)


class EvalRun(Base):
    """A stored eval run: summary columns for listing plus the full per-case result."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    suite: Mapped[str] = mapped_column(String(200), index=True)
    model: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    pass_rate: Mapped[float] = mapped_column(Float)
    duration_s: Mapped[float] = mapped_column(Float)
    total_cases: Mapped[int] = mapped_column(Integer)
    passed_cases: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
