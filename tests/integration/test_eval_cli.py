from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from typer.testing import CliRunner

from switchboard.api.app import create_app
from switchboard.cli import app as cli
from switchboard.config import Settings

pytestmark = pytest.mark.integration

SMOKE = Path(__file__).resolve().parents[2] / "examples" / "evals" / "smoke.yaml"
Serve = Callable[[FastAPI], AbstractContextManager[str]]


@pytest.fixture
def gateway(config_factory: Callable[..., Any], serve: Serve) -> Any:
    """A live, key-less gateway with only the mock provider (no Postgres needed)."""
    app = create_app(Settings(database_url=None, auth_enabled=False), config_factory())
    with serve(app) as base_url:
        yield base_url


def suite_file(tmp_path: Path, *, expects: str, critical: bool) -> Path:
    path = tmp_path / "suite.yaml"
    cases = [
        {
            "id": f"case-{i}",
            "input": f"question {i}",
            "critical": critical,
            "assert": [{"type": "contains", "value": expects}],
        }
        for i in range(3)
    ]
    path.write_text(yaml.safe_dump({"name": "demo", "model": "mock", "cases": cases}))
    return path


def run(*args: str | Path) -> Any:
    return CliRunner().invoke(cli, ["eval", "run", *map(str, args)])


def test_smoke_suite_passes_against_a_live_gateway(gateway: str, tmp_path: Path) -> None:
    result = run(SMOKE, "--base-url", gateway, "--out", tmp_path / "out", "--min-pass-rate", "1.0")

    assert result.exit_code == 0, result.output
    saved = json.loads((tmp_path / "out" / "smoke.json").read_text())
    assert (saved["total_cases"], saved["pass_rate"]) == (3, 1.0)
    assert all(case["output"].startswith("echo: ") for case in saved["cases"])


def test_failed_critical_case_exits_nonzero(gateway: str, tmp_path: Path) -> None:
    suite = suite_file(tmp_path, expects="never echoed", critical=True)
    assert run(suite, "--base-url", gateway, "--out", tmp_path / "out").exit_code == 1


def test_low_pass_rate_only_fails_when_a_minimum_is_set(gateway: str, tmp_path: Path) -> None:
    suite = suite_file(tmp_path, expects="never echoed", critical=False)
    args = (suite, "--base-url", gateway, "--out", tmp_path / "out")
    assert run(*args).exit_code == 0
    assert run(*args, "--min-pass-rate", "0.5").exit_code == 1


def test_regression_against_a_baseline_directory(gateway: str, tmp_path: Path) -> None:
    healthy = suite_file(tmp_path, expects="question", critical=False)
    assert run(healthy, "--base-url", gateway, "--out", tmp_path / "baseline").exit_code == 0

    degraded = suite_file(tmp_path, expects="never echoed", critical=False)
    args = (degraded, "--base-url", gateway, "--out", tmp_path / "out", "--baseline")
    assert run(*args, tmp_path / "baseline").exit_code == 1  # 100% -> 0%
    assert run(*args, tmp_path / "baseline", "--max-regression", "1.0").exit_code == 0
    assert run(*args, tmp_path / "missing-dir").exit_code == 0  # no baseline, nothing to compare


def test_markdown_summary_is_appended_for_ci(gateway: str, tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    summary.write_text("# Existing job summary\n")
    for _ in range(2):
        assert (
            run(SMOKE, "--base-url", gateway, "--out", tmp_path, "--summary-md", summary).exit_code
            == 0
        )

    text = summary.read_text()
    assert text.startswith("# Existing job summary")
    assert text.count("### Eval: smoke (mock)") == 2
    assert "echoes-the-prompt" in text


def test_unreachable_gateway_fails_cases_instead_of_crashing(tmp_path: Path) -> None:
    result = run(
        SMOKE, "--base-url", "http://127.0.0.1:1", "--out", tmp_path, "--min-pass-rate", "1"
    )
    assert result.exit_code == 1
    saved = json.loads((tmp_path / "smoke.json").read_text())
    assert saved["passed_cases"] == 0 and all(c["error"] for c in saved["cases"])
