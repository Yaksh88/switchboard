from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from switchboard.api.app import create_app
from switchboard.config import GatewayConfig, Settings
from switchboard.context import RequestContext
from switchboard.db.engine import session_factory
from switchboard.observability.log_writer import LogWriter
from switchboard.observability.metrics import REGISTRY

pytestmark = pytest.mark.integration

Sql = Callable[..., list[dict[str, Any]]]
ConfigFactory = Callable[..., GatewayConfig]
Serve = Callable[[FastAPI], AbstractContextManager[str]]

PRICED_MODELS = {
    "mock": {
        "targets": [
            {
                "provider": "mock",
                "model": "mock-1",
                "input_cost_per_mtok": 1000.0,
                "output_cost_per_mtok": 2000.0,
            }
        ]
    }
}


def app_for(db_url: str, redis_url: str, config: GatewayConfig) -> FastAPI:
    settings = Settings(database_url=db_url, redis_url=redis_url, auth_enabled=False)
    return create_app(settings, config)


def say(client: TestClient, text_: str = "hello world") -> Any:
    return client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [{"role": "user", "content": text_}]},
    )


def test_each_request_writes_one_log_row(
    clean_db: str, redis_url: str, config_factory: ConfigFactory, sql: Sql
) -> None:
    app = app_for(clean_db, redis_url, config_factory(models=PRICED_MODELS))
    with TestClient(app) as client:
        first, second = say(client), say(client, "another one")
    ids = [first.headers["x-request-id"], second.headers["x-request-id"]]

    rows = sql("select * from request_logs order by id")
    assert [r["request_id"] for r in rows] == ids
    row = rows[0]
    assert (row["model_alias"], row["provider"], row["upstream_model"]) == (
        "mock",
        "mock",
        "mock-1",
    )
    assert (row["status_code"], row["cache"], row["attempts"], row["stream"]) == (
        200,
        "bypass",
        1,
        False,
    )
    assert row["prompt_tokens"] > 0 and row["completion_tokens"] > 0 and row["latency_ms"] > 0
    expected = (row["prompt_tokens"] * 1000 + row["completion_tokens"] * 2000) / 1_000_000
    assert float(row["cost_usd"]) == pytest.approx(expected, abs=1e-6)


def test_cached_responses_are_logged_as_free(
    clean_db: str, redis_url: str, config_factory: ConfigFactory, sql: Sql, sync_redis: Any
) -> None:
    config = config_factory(models=PRICED_MODELS, cache={"enabled": True})
    with TestClient(app_for(clean_db, redis_url, config)) as client:
        say(client)
        say(client)

    first, second = sql("select * from request_logs order by id")
    assert float(first["cost_usd"]) > 0
    assert (second["cache"], second["provider"], float(second["cost_usd"])) == (
        "exact",
        "cache",
        0.0,
    )


def test_failed_request_is_logged_with_error(
    clean_db: str, redis_url: str, config_factory: ConfigFactory, sql: Sql
) -> None:
    config = config_factory(
        providers={"mock": {"type": "mock", "failure_rate": 1.0, "max_retries": 0}}
    )
    with TestClient(app_for(clean_db, redis_url, config)) as client:
        assert say(client).status_code == 503

    (row,) = sql("select * from request_logs")
    assert row["status_code"] == 503
    assert "simulated upstream failure" in row["error"]
    assert row["prompt_tokens"] == 0 and float(row["cost_usd"]) == 0


def test_streamed_request_logs_time_to_first_token(
    clean_db: str, redis_url: str, config_factory: ConfigFactory, sql: Sql
) -> None:
    with TestClient(app_for(clean_db, redis_url, config_factory())) as client:
        body = {"model": "mock", "stream": True, "messages": [{"role": "user", "content": "a b"}]}
        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            list(resp.iter_lines())

    (row,) = sql("select * from request_logs")
    assert row["stream"] is True and row["status_code"] == 200
    assert 0 < row["ttft_ms"] <= row["latency_ms"]
    assert row["completion_tokens"] > 0


async def test_log_writer_flushes_on_close(clean_db: str) -> None:
    engine = create_async_engine(clean_db)
    writer = LogWriter(session_factory(engine), flush_interval_s=30)
    await writer.start()
    for _ in range(5):
        writer.submit(RequestContext(model_alias="m"))
    await writer.close()  # must not wait out the 30s interval, and must not lose rows

    async with engine.connect() as conn:
        count = (await conn.execute(text("select count(*) from request_logs"))).scalar_one()
    await engine.dispose()
    assert count == 5


async def test_log_writer_drops_when_full(clean_db: str) -> None:
    engine = create_async_engine(clean_db)
    writer = LogWriter(session_factory(engine), max_queue=2)  # never started: nothing drains
    before = REGISTRY.get_sample_value("switchboard_log_dropped_total") or 0

    for _ in range(5):
        writer.submit(RequestContext(model_alias="m"))  # must not block or raise

    assert (REGISTRY.get_sample_value("switchboard_log_dropped_total") or 0) == before + 3
    await engine.dispose()


def test_stream_client_disconnect_logs_499(
    clean_db: str,
    redis_url: str,
    config_factory: ConfigFactory,
    sql: Sql,
    sync_redis: Any,
    serve: Serve,
) -> None:
    config = config_factory(
        providers={"mock": {"type": "mock", "chunk_delay_ms": 100}},
        cache={"enabled": True},
    )
    words = " ".join(f"w{i}" for i in range(40))  # ~4s of streaming if left alone
    body = {"model": "mock", "stream": True, "messages": [{"role": "user", "content": words}]}

    with serve(app_for(clean_db, redis_url, config)) as base_url:
        with httpx.stream("POST", f"{base_url}/v1/chat/completions", json=body) as resp:
            request_id = resp.headers["x-request-id"]
            for line in resp.iter_lines():
                if line.startswith("data:"):
                    break  # got the first chunk; now hang up
        time.sleep(0.5)

    (row,) = sql("select * from request_logs where request_id = :id", id=request_id)
    assert row["status_code"] == 499
    assert row["error"] == "client disconnected"
    # A half-delivered answer must never be cached.
    assert sync_redis.keys("sb:cache:*") == []
