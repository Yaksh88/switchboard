from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from switchboard.config import Settings

pytestmark = pytest.mark.integration

ADMIN = {"authorization": "Bearer test-admin"}


@pytest.fixture
def api(
    make_client: Callable[..., TestClient], clean_db: str, redis_url: str, sync_redis: Any
) -> TestClient:
    return make_client(
        Settings(
            database_url=clean_db, redis_url=redis_url, auth_enabled=True, admin_token="test-admin"
        )
    )


def new_key(client: TestClient, **body: Any) -> tuple[UUID, str]:
    created = client.post("/admin/keys", json={"name": "k", **body}, headers=ADMIN).json()
    return UUID(created["id"]), str(created["key"])


def chat(client: TestClient, key: str) -> Any:
    return client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [{"role": "user", "content": "hi"}]},
        headers={"authorization": f"Bearer {key}"},
    )


def test_key_over_its_monthly_budget_is_rejected(
    api: TestClient, seed_log: Callable[..., None]
) -> None:
    key_id, key = new_key(api, monthly_budget_usd=0.001)
    seed_log(api_key_id=key_id, cost_usd=0.002)

    resp = chat(api, key)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "budget_exceeded"
    assert int(resp.headers["retry-after"]) > 0


def test_spend_from_previous_months_does_not_count(
    api: TestClient, seed_log: Callable[..., None]
) -> None:
    key_id, key = new_key(api, monthly_budget_usd=0.001)
    last_month = datetime.now(UTC).replace(day=1) - timedelta(days=1)
    seed_log(api_key_id=key_id, cost_usd=5, created_at=last_month)
    assert chat(api, key).status_code == 200


def test_spend_is_tracked_per_key(api: TestClient, seed_log: Callable[..., None]) -> None:
    spender, spender_key = new_key(api, monthly_budget_usd=0.001)
    _, other_key = new_key(api, monthly_budget_usd=0.001)
    seed_log(api_key_id=spender, cost_usd=1)

    assert chat(api, spender_key).status_code == 429
    assert chat(api, other_key).status_code == 200


def test_keys_without_a_budget_are_unlimited(
    api: TestClient, seed_log: Callable[..., None]
) -> None:
    key_id, key = new_key(api)
    seed_log(api_key_id=key_id, cost_usd=1000)
    assert chat(api, key).status_code == 200
