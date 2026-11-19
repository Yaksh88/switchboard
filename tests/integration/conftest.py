from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import redis as sync_redis_lib
from alembic import command
from alembic.config import Config
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
