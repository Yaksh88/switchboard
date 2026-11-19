from __future__ import annotations

from dataclasses import dataclass

from rich.table import Table

from switchboard.evals.runner import CaseResult, RunResult


@dataclass
class Comparison:
    baseline_pass_rate: float
    current_pass_rate: float
    regressed_cases: list[str]
    fixed_cases: list[str]
    critical_failures: list[str]

    def exceeds_drop(self, max_drop: float) -> bool:
        """Whether the pass rate fell by more than ``max_drop`` (absolute, 0-1)."""
        return self.baseline_pass_rate - self.current_pass_rate > max_drop + 1e-9

    def is_regression(self, max_drop: float) -> bool:
        """A drop beyond ``max_drop`` or any failing critical case."""
        return self.exceeds_drop(max_drop) or bool(self.critical_failures)


def compare(current: RunResult, baseline: RunResult) -> Comparison:
    before = {c.case_id: c.passed for c in baseline.cases}
    return Comparison(
        baseline_pass_rate=baseline.pass_rate,
        current_pass_rate=current.pass_rate,
        regressed_cases=[
            c.case_id for c in current.cases if before.get(c.case_id) and not c.passed
        ],
        fixed_cases=[
            c.case_id for c in current.cases if before.get(c.case_id) is False and c.passed
        ],
        critical_failures=[c.case_id for c in current.cases if c.critical and not c.passed],
    )


def _details(case: CaseResult) -> str:
    if case.error:
        return f"error: {case.error}"
    return "; ".join(f"{kind}: {s.reason}" for kind, s in case.scores if not s.passed)


def _status(case: CaseResult) -> str:
    return "pass" if case.passed else ("FAIL (critical)" if case.critical else "FAIL")


def _summary(result: RunResult, comparison: Comparison | None) -> str:
    passed = sum(c.passed for c in result.cases)
    line = f"{result.pass_rate:.0%} passed ({passed}/{len(result.cases)})"
    if comparison is not None:
        delta = comparison.current_pass_rate - comparison.baseline_pass_rate
        line += f" · baseline {comparison.baseline_pass_rate:.0%} ({delta:+.0%})"
    return line


def render_table(result: RunResult, comparison: Comparison | None = None) -> Table:
    table = Table(
        title=f"{result.suite} · {result.model}",
        caption=_summary(result, comparison),
        show_lines=False,
    )
    for column in ("case", "result", "latency", "cost", "details"):
        table.add_column(column, overflow="fold")
    for case in result.cases:
        style = None if case.passed else "red"
        table.add_row(
            case.case_id,
            _status(case),
            f"{case.latency_ms:.0f}ms",
            f"${case.cost_usd:.5f}" if case.cost_usd is not None else "-",
            _details(case),
            style=style,
        )
    return table


def render_markdown(result: RunResult, comparison: Comparison | None = None) -> str:
    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    lines = [
        f"### Eval: {result.suite} ({result.model})",
        "",
        f"**{_summary(result, comparison)}** in {result.duration_s:.1f}s",
        "",
    ]
    if comparison is not None:
        if comparison.regressed_cases:
            lines += [f"Regressed: {', '.join(comparison.regressed_cases)}", ""]
        if comparison.fixed_cases:
            lines += [f"Fixed: {', '.join(comparison.fixed_cases)}", ""]
    lines += ["| Case | Result | Latency | Details |", "| --- | --- | --- | --- |"]
    for case in result.cases:
        lines.append(
            f"| {cell(case.case_id)} | {_status(case)} | {case.latency_ms:.0f}ms "
            f"| {cell(_details(case))} |"
        )
    return "\n".join(lines) + "\n"
