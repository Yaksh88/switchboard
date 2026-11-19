from __future__ import annotations

from datetime import UTC, datetime

import pytest

from switchboard.evals.report import compare, render_markdown, render_table
from switchboard.evals.runner import CaseResult, RunResult
from switchboard.evals.scorers import ScoreResult


def case(case_id: str, passed: bool, *, critical: bool = False) -> CaseResult:
    reason = "ok" if passed else "missing ['x']"
    return CaseResult(
        case_id,
        passed,
        critical,
        "out",
        12.0,
        0.0002,
        [("contains", ScoreResult(passed, 1.0, reason))],
    )


def run(*cases: CaseResult) -> RunResult:
    return RunResult("suite", "model", datetime(2026, 1, 1, tzinfo=UTC), 1.5, list(cases))


def ten(failing: int) -> RunResult:
    return run(*(case(f"c{i}", i >= failing) for i in range(10)))


def test_compare_finds_regressed_and_fixed_cases() -> None:
    baseline = run(case("a", True), case("b", False), case("c", True))
    current = run(case("a", False), case("b", True), case("c", True), case("new", False))

    result = compare(current, baseline)
    assert result.regressed_cases == ["a"]
    assert result.fixed_cases == ["b"]  # a brand new failing case is neither
    assert (result.baseline_pass_rate, result.current_pass_rate) == (2 / 3, 0.5)


@pytest.mark.parametrize(("failing", "expected"), [(0, False), (1, False), (5, True)])
def test_is_regression_respects_max_drop(failing: int, expected: bool) -> None:
    baseline = run(*(case(f"c{i}", True) for i in range(10)))
    assert compare(ten(failing), baseline).is_regression(max_drop=0.10) is expected


def test_drop_just_over_the_limit_is_a_regression() -> None:
    baseline = ten(0)
    assert compare(ten(1), baseline).is_regression(max_drop=0.05)  # 10% drop > 5%
    assert not compare(ten(1), baseline).is_regression(max_drop=0.10)  # exactly at the limit


def test_any_critical_failure_is_a_regression_even_when_the_rate_improves() -> None:
    baseline = run(case("a", False), case("b", False), case("crit", True, critical=True))
    current = run(case("a", True), case("b", True), case("crit", False, critical=True))
    result = compare(current, baseline)
    assert result.critical_failures == ["crit"]
    assert result.is_regression(max_drop=1.0) is True


def test_improvement_is_not_a_regression() -> None:
    assert not compare(ten(0), ten(4)).is_regression(max_drop=0.0)


def test_markdown_has_a_row_per_case_and_escapes_pipes() -> None:
    current = run(case("good", True), case("bad", False), case("crit", False, critical=True))
    current.cases[1].error = "a | b"
    text = render_markdown(current, compare(current, run(case("good", True), case("bad", True))))

    rows = [line for line in text.splitlines() if line.startswith("| ") and "---" not in line]
    assert len(rows) == 1 + 3  # header + one per case
    assert "FAIL (critical)" in text
    assert "a \\| b" in text
    assert "Regressed: bad" in text
    assert "33% passed (1/3)" in text and "baseline 100%" in text


def test_table_has_a_row_per_case_and_a_summary() -> None:
    result = run(case("a", True), case("b", False))
    table = render_table(result)
    assert table.row_count == 2
    assert table.caption == "50% passed (1/2)"
