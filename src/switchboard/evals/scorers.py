from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator

from switchboard.evals.suite import Assertion, EvalCase
from switchboard.schemas import Usage


@dataclass(frozen=True)
class ScoreResult:
    passed: bool
    score: float
    reason: str


@dataclass(frozen=True)
class Completion:
    text: str
    latency_ms: float
    usage: Usage


# (rubric, prompt, answer) -> (score between 0 and 1, reason)
JudgeFn = Callable[[str, str, str], Awaitable[tuple[float, str]]]

_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def _result(passed: bool, reason: str) -> ScoreResult:
    return ScoreResult(passed, 1.0 if passed else 0.0, reason)


def _fold(text: str, assertion: Assertion) -> str:
    return text if assertion.case_sensitive else text.casefold()


def _parse_json(text: str) -> Any:
    stripped = text.strip()
    if match := _FENCE.match(stripped):
        stripped = match.group(1)
    return json.loads(stripped)


def parse_judge_reply(text: str) -> tuple[float, str]:
    """Pull ``{"score": ..., "reason": ...}`` out of a judge reply, tolerating fences/chatter."""
    invalid = (0.0, "judge returned invalid JSON")
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match is None:
        return invalid
    try:
        data = json.loads(match.group(0))
        return min(1.0, max(0.0, float(data["score"]))), str(data.get("reason", ""))
    except (ValueError, KeyError, TypeError):
        return invalid


def _schema_error_path(error: Any) -> str:
    path = "$"
    for part in error.absolute_path:
        path += f"[{part}]" if isinstance(part, int) else f".{part}"
    return path


async def score(
    assertion: Assertion, case: EvalCase, completion: Completion, judge: JudgeFn | None
) -> ScoreResult:
    text, kind = completion.text, assertion.type

    if kind == "equals":
        expected = str(assertion.value)
        ok = _fold(text.strip(), assertion) == _fold(expected.strip(), assertion)
        return _result(ok, "matches" if ok else f"expected {expected!r}, got {text.strip()!r}")

    if kind in ("contains", "not_contains"):
        needles = assertion.value if isinstance(assertion.value, list) else [assertion.value]
        haystack = _fold(text, assertion)
        present = [str(n) for n in needles if _fold(str(n), assertion) in haystack]
        if kind == "contains":
            missing = [str(n) for n in needles if str(n) not in present]
            return _result(not missing, "found" if not missing else f"missing {missing}")
        return _result(not present, "absent" if not present else f"unexpectedly contains {present}")

    if kind == "regex":
        flags = 0 if assertion.case_sensitive else re.IGNORECASE
        ok = re.search(str(assertion.value), text, flags) is not None
        return _result(ok, "matches" if ok else f"no match for /{assertion.value}/")

    if kind == "json_valid":
        try:
            _parse_json(text)
        except ValueError as exc:
            return _result(False, f"not valid JSON: {exc}")
        return _result(True, "valid JSON")

    if kind == "json_schema":
        try:
            data = _parse_json(text)
        except ValueError as exc:
            return _result(False, f"not valid JSON: {exc}")
        errors = sorted(
            Draft202012Validator(assertion.json_schema or {}).iter_errors(data),
            key=lambda e: list(e.absolute_path),
        )
        if errors:
            return _result(False, f"{_schema_error_path(errors[0])}: {errors[0].message}")
        return _result(True, "matches schema")

    if kind == "max_latency_ms":
        limit = float(assertion.value)
        ok = completion.latency_ms <= limit
        return _result(ok, f"{completion.latency_ms:.0f}ms {'<=' if ok else '>'} {limit:.0f}ms")

    # llm_judge
    if judge is None:
        return _result(False, "no judge available")
    graded, reason = await judge(assertion.rubric or "", case.prompt_text(), text)
    return ScoreResult(graded >= assertion.threshold, graded, reason)
