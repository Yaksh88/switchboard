from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from switchboard.api.app import create_app
from switchboard.cli import app as cli
from switchboard.config import GatewayConfig, Settings

pytestmark = pytest.mark.integration

ADMIN = {"authorization": "Bearer test-admin"}
SMOKE = Path(__file__).resolve().parents[2] / "examples" / "evals" / "smoke.yaml"


@pytest.fixture
def api(
    make_client: Callable[..., TestClient], clean_db: str, redis_url: str, sync_redis: Any
) -> TestClient:
    return make_client(
        Settings(
            database_url=clean_db, redis_url=redis_url, auth_enabled=False, admin_token="test-admin"
        )
    )


def payload(suite: str = "support", *, passed: int = 1, total: int = 2) -> dict[str, Any]:
    return {
        "suite": suite,
        "model": "gpt-4o-mini",
        "started_at": datetime.now(UTC).isoformat(),
        "duration_s": 1.25,
        "pass_rate": passed / total,
        "total_cases": total,
        "passed_cases": passed,
        "cases": [
            {
                "case_id": "refund",
                "passed": True,
                "critical": True,
                "output": "30 days",
                "latency_ms": 420.0,
                "cost_usd": 0.0002,
                "error": None,
                "scores": [{"type": "contains", "passed": True, "score": 1.0, "reason": "found"}],
            },
            {
                "case_id": "tone",
                "passed": False,
                "critical": False,
                "output": "no",
                "latency_ms": 380.0,
                "cost_usd": None,
                "error": None,
                "scores": [{"type": "llm_judge", "passed": False, "score": 0.4, "reason": "curt"}],
            },
        ],
    }


def test_store_then_list_and_fetch_a_run(api: TestClient) -> None:
    created = api.post("/admin/api/evals", json=payload(), headers=ADMIN)
    assert created.status_code == 201
    run_id = created.json()["id"]

    (listed,) = api.get("/admin/api/evals", headers=ADMIN).json()
    assert listed["id"] == run_id
    assert (listed["suite"], listed["pass_rate"], listed["total_cases"]) == ("support", 0.5, 2)
    assert "result" not in listed

    detail = api.get(f"/admin/api/evals/{run_id}", headers=ADMIN).json()
    assert [c["case_id"] for c in detail["result"]["cases"]] == ["refund", "tone"]
    assert detail["result"]["cases"][1]["scores"][0]["reason"] == "curt"


def test_list_filters_by_suite_newest_first_and_honours_limit(api: TestClient) -> None:
    for suite in ("a", "b", "a"):
        api.post("/admin/api/evals", json=payload(suite), headers=ADMIN)

    only_a = api.get("/admin/api/evals", params={"suite": "a"}, headers=ADMIN).json()
    assert [r["suite"] for r in only_a] == ["a", "a"]
    assert only_a[0]["created_at"] >= only_a[1]["created_at"]
    assert len(api.get("/admin/api/evals", params={"limit": 1}, headers=ADMIN).json()) == 1


def test_malformed_body_and_unknown_run(api: TestClient) -> None:
    assert api.post("/admin/api/evals", json={"suite": "x"}, headers=ADMIN).status_code == 400
    bad = payload()
    bad["pass_rate"] = 1.5
    assert api.post("/admin/api/evals", json=bad, headers=ADMIN).status_code == 400
    assert api.get(f"/admin/api/evals/{uuid.uuid4()}", headers=ADMIN).status_code == 404


def test_eval_endpoints_require_the_admin_token(api: TestClient) -> None:
    assert api.get("/admin/api/evals").status_code == 401
    assert api.post("/admin/api/evals", json=payload()).status_code == 401


def test_cli_report_flag_uploads_the_run(
    clean_db: str,
    redis_url: str,
    sync_redis: Any,
    config_factory: Callable[..., GatewayConfig],
    serve: Callable[[FastAPI], AbstractContextManager[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        database_url=clean_db, redis_url=redis_url, auth_enabled=False, admin_token="test-admin"
    )
    monkeypatch.setenv("SWITCHBOARD_ADMIN_TOKEN", "test-admin")

    with serve(create_app(settings, config_factory())) as base_url:
        result = CliRunner().invoke(
            cli,
            ["eval", "run", str(SMOKE), "--base-url", base_url, "--out", str(tmp_path), "--report"],
        )
        assert result.exit_code == 0, result.output
        runs = httpx.get(f"{base_url}/admin/api/evals", headers=ADMIN).json()

    assert [(r["suite"], r["model"], r["pass_rate"]) for r in runs] == [("smoke", "mock", 1.0)]
