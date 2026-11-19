from __future__ import annotations

import asyncio
import itertools
import socket
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import redis as sync_redis_lib
import uvicorn
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from switchboard.db.models import Base
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


DB_URL = "postgresql+asyncpg://localhost/switchboard_test"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def db_url() -> str:
    """Test database with migrations applied; skips when Postgres isn't reachable."""

    async def ping() -> None:
        engine = create_async_engine(DB_URL)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("select 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(ping())
    except (OSError, SQLAlchemyError):
        pytest.skip("postgres unavailable")

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", DB_URL)
    command.upgrade(config, "head")
    return DB_URL


@pytest.fixture
def clean_db(db_url: str) -> str:
    async def truncate() -> None:
        engine = create_async_engine(db_url)
        try:
            async with engine.begin() as conn:
                tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
                await conn.execute(text(f"truncate {tables} restart identity cascade"))
        finally:
            await engine.dispose()

    asyncio.run(truncate())
    return db_url


@pytest.fixture
def sql(clean_db: str) -> Callable[..., list[dict[str, Any]]]:
    """Run SQL against the test database from a synchronous test."""

    def run(statement: str, **params: Any) -> list[dict[str, Any]]:
        async def go() -> list[dict[str, Any]]:
            engine = create_async_engine(clean_db)
            try:
                async with engine.begin() as conn:
                    result = await conn.execute(text(statement), params)
                    return [dict(r) for r in result.mappings().all()] if result.returns_rows else []
            finally:
                await engine.dispose()

        return asyncio.run(go())

    return run


@pytest.fixture
def seed_log(sql: Callable[..., list[dict[str, Any]]]) -> Callable[..., None]:
    """Insert one request_logs row; override any column by keyword."""
    counter = itertools.count(1)

    def seed(**overrides: Any) -> None:
        row: dict[str, Any] = {
            "request_id": f"req_seed{next(counter):04d}",
            "created_at": datetime.now(UTC),
            "api_key_id": None,
            "model_alias": "mock",
            "provider": "mock",
            "upstream_model": "mock-1",
            "status_code": 200,
            "cache": "miss",
            "attempts": 1,
            "stream": False,
            "latency_ms": 100.0,
            "ttft_ms": None,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost_usd": 0,
            "error": None,
            **overrides,
        }
        columns = ", ".join(row)
        values = ", ".join(f":{name}" for name in row)
        sql(f"insert into request_logs ({columns}) values ({values})", **row)

    return seed


@pytest.fixture
def serve() -> Callable[[FastAPI], AbstractContextManager[str]]:
    """Run an app under a real uvicorn so client disconnects and the CLI behave as in production."""

    @contextmanager
    def run(app: FastAPI) -> Iterator[str]:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            assert time.monotonic() < deadline, "server did not start"
            time.sleep(0.02)
        try:
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=15)

    return run
