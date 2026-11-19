from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis import Redis as SyncRedis

from switchboard.config import Settings

pytestmark = pytest.mark.integration

ADMIN = {"authorization": "Bearer test-admin"}


@pytest.fixture
def auth_client(
    make_client: Callable[..., TestClient], clean_db: str, redis_url: str, sync_redis: SyncRedis
) -> TestClient:
    settings = Settings(
        database_url=clean_db, redis_url=redis_url, auth_enabled=True, admin_token="test-admin"
    )
    return make_client(settings)


def create_key(client: TestClient, **body: Any) -> dict[str, Any]:
    resp = client.post("/admin/keys", json={"name": "test", **body}, headers=ADMIN)
    assert resp.status_code == 201, resp.text
    created: dict[str, Any] = resp.json()
    return created


def chat(client: TestClient, key: str | None, model: str = "mock") -> Any:
    headers = {"authorization": f"Bearer {key}"} if key else {}
    return client.post(
        "/v1/chat/completions",
        json={"model": model, "messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )


def test_missing_or_invalid_key_is_401(auth_client: TestClient) -> None:
    for key in (None, "sb-not-a-real-key"):
        resp = chat(auth_client, key)
        assert resp.status_code == 401
        assert resp.json()["error"]["type"] == "authentication_error"


def test_models_endpoint_requires_a_key(auth_client: TestClient) -> None:
    assert auth_client.get("/v1/models").status_code == 401


def test_valid_key_can_call_and_revoked_key_cannot(auth_client: TestClient) -> None:
    created = create_key(auth_client)
    assert chat(auth_client, created["key"]).status_code == 200

    resp = auth_client.delete(f"/admin/keys/{created['id']}", headers=ADMIN)
    assert resp.status_code == 204
    assert chat(auth_client, created["key"]).status_code == 401
    assert auth_client.delete(f"/admin/keys/{created['id']}", headers=ADMIN).status_code == 404


def test_model_allowlist_is_enforced(auth_client: TestClient) -> None:
    created = create_key(auth_client, allowed_models=["other"])
    resp = chat(auth_client, created["key"], model="mock")
    assert resp.status_code == 403
    assert resp.json()["error"]["type"] == "permission_error"


def test_admin_endpoints_require_admin_token(
    auth_client: TestClient, make_client: Callable[..., TestClient], clean_db: str
) -> None:
    assert auth_client.get("/admin/keys").status_code == 401
    assert (
        auth_client.get("/admin/keys", headers={"authorization": "Bearer nope"}).status_code == 401
    )

    no_admin = make_client(Settings(database_url=clean_db, auth_enabled=True, admin_token=None))
    assert no_admin.get("/admin/keys", headers=ADMIN).status_code == 503


def test_raw_key_is_only_returned_on_creation(auth_client: TestClient) -> None:
    created = create_key(auth_client, rpm_limit=5)
    assert created["key"].startswith("sb-")
    assert created["key_prefix"] == created["key"][:12]

    listed = auth_client.get("/admin/keys", headers=ADMIN).json()
    assert [k["id"] for k in listed] == [created["id"]]
    assert "key" not in listed[0]
    assert "key_hash" not in listed[0]
    assert listed[0]["rpm_limit"] == 5


def test_create_key_validates_input(auth_client: TestClient) -> None:
    resp = auth_client.post("/admin/keys", json={"name": "", "rpm_limit": 0}, headers=ADMIN)
    assert resp.status_code == 400


def test_auth_disabled_allows_anonymous_requests(
    make_client: Callable[..., TestClient],
) -> None:
    client = make_client(Settings(database_url=None, auth_enabled=False))
    assert chat(client, None).status_code == 200


def test_enabling_auth_without_a_database_is_a_startup_error(gateway_config: Any) -> None:
    from switchboard.api.app import create_app

    with pytest.raises(ValueError, match="no database"):
        create_app(Settings(database_url=None, auth_enabled=True), gateway_config)
