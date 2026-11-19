from __future__ import annotations

import itertools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from switchboard.config import Settings

pytestmark = pytest.mark.integration

ADMIN = {"authorization": "Bearer test-admin"}
SeedLog = Callable[..., None]


@pytest.fixture
def api(
    make_client: Callable[..., TestClient], clean_db: str, redis_url: str, sync_redis: Any
) -> TestClient:
    return make_client(
        Settings(
            database_url=clean_db, redis_url=redis_url, auth_enabled=False, admin_token="test-admin"
        )
    )


@pytest.fixture
def seeded(api: TestClient, seed_log: SeedLog) -> TestClient:
    """20 requests spread over the last 30 minutes: 12 on `gpt`, 8 on `claude`."""
    now = datetime.now(UTC)
    for i in range(12):
        seed_log(
            model_alias="gpt",
            created_at=now - timedelta(minutes=i),
            status_code=500 if i < 2 else 200,
            cost_usd=0.01,
            latency_ms=100.0 + i,
        )
    for i in range(8):
        seed_log(
            model_alias="claude",
            created_at=now - timedelta(minutes=12 + i),
            cache="exact" if i < 2 else "miss",
            cost_usd=0.0 if i < 2 else 0.02,
            latency_ms=200.0,
        )
    return api


def get(client: TestClient, path: str, **params: Any) -> Any:
    resp = client.get(path, params=params, headers=ADMIN)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_overview_totals(seeded: TestClient) -> None:
    totals = get(seeded, "/admin/api/overview", window="1h")["totals"]
    assert totals["requests"] == 20
    assert totals["errors"] == 2
    assert totals["error_rate"] == pytest.approx(0.1)
    assert totals["cost_usd"] == pytest.approx(12 * 0.01 + 6 * 0.02)
    assert totals["cache_hit_rate"] == pytest.approx(0.1)
    assert totals["prompt_tokens"] == 200 and totals["completion_tokens"] == 100
    assert 100 <= totals["p50_ms"] <= 200 and totals["p95_ms"] >= totals["p50_ms"]


@pytest.mark.parametrize("window", ["1h", "24h", "7d"])
def test_overview_series_covers_the_window_without_losing_requests(
    seeded: TestClient, window: str
) -> None:
    series = get(seeded, "/admin/api/overview", window=window)["series"]
    assert sum(p["requests"] for p in series) == 20
    assert sum(p["errors"] for p in series) == 2
    starts = [datetime.fromisoformat(p["ts"]) for p in series]
    assert starts == sorted(starts)
    assert len({b - a for a, b in itertools.pairwise(starts)}) == 1  # evenly spaced


def test_overview_of_an_empty_window_is_zeroed(api: TestClient) -> None:
    data = get(api, "/admin/api/overview", window="1h")
    assert data["totals"]["requests"] == 0 and data["totals"]["error_rate"] == 0
    assert all(p["requests"] == 0 for p in data["series"])


def test_models_breakdown_is_ordered_by_traffic(seeded: TestClient) -> None:
    gpt, claude = get(seeded, "/admin/api/models", window="24h")
    assert (gpt["model"], gpt["requests"]) == ("gpt", 12)
    assert gpt["error_rate"] == pytest.approx(2 / 12)
    assert (claude["model"], claude["requests"]) == ("claude", 8)
    assert claude["cache_hit_rate"] == pytest.approx(2 / 8)


def test_request_pages_are_disjoint_and_complete(seeded: TestClient) -> None:
    seen: list[int] = []
    before: int | None = None
    while True:
        params: dict[str, Any] = {"limit": 8}
        if before is not None:
            params["before_id"] = before
        page = get(seeded, "/admin/api/requests", **params)
        seen += [item["id"] for item in page["items"]]
        before = page["next_before_id"]
        if before is None:
            break
    assert len(seen) == 20 and len(set(seen)) == 20
    assert seen == sorted(seen, reverse=True)


def test_request_filters(seeded: TestClient) -> None:
    def count(**params: Any) -> int:
        return len(get(seeded, "/admin/api/requests", limit=200, **params)["items"])

    assert count(status="error") == 2
    assert count(status="ok") == 18
    assert count(model="claude") == 8
    assert count(cache="exact") == 2


def test_request_detail_and_missing_request(seeded: TestClient) -> None:
    item = get(seeded, "/admin/api/requests", limit=1)["items"][0]
    detail = get(seeded, f"/admin/api/requests/{item['request_id']}")
    assert detail == item
    assert seeded.get("/admin/api/requests/nope", headers=ADMIN).status_code == 404


def test_invalid_window_is_rejected(api: TestClient) -> None:
    assert api.get("/admin/api/overview", params={"window": "1y"}, headers=ADMIN).status_code == 400


def test_stats_require_the_admin_token(api: TestClient) -> None:
    assert api.get("/admin/api/overview").status_code == 401


def test_providers_reports_breaker_state(api: TestClient) -> None:
    assert get(api, "/admin/api/providers") == [
        {"provider": "mock", "model": "mock-1", "state": "closed"}
    ]


def test_metrics_endpoint_is_public_prometheus_text(api: TestClient) -> None:
    api.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
    )
    resp = api.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert "switchboard_requests_total" in resp.text
