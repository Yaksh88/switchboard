from __future__ import annotations

from typing import Any

from switchboard.evals.scorers import Completion, ScoreResult, parse_judge_reply, score
from switchboard.evals.suite import Assertion, EvalCase
from switchboard.schemas import Usage

CASE = EvalCase.model_validate(
    {"id": "c", "input": "the question", "assert": [{"type": "contains", "value": "x"}]}
)


async def run(
    text: str, judge: Any = None, latency_ms: float = 10, **assertion: Any
) -> ScoreResult:
    return await score(
        Assertion.model_validate(assertion), CASE, Completion(text, latency_ms, Usage()), judge
    )


async def test_equals_ignores_case_and_surrounding_whitespace_by_default() -> None:
    assert (await run("  Hello ", type="equals", value="hello")).passed
    assert not (await run("  Hello ", type="equals", value="hello", case_sensitive=True)).passed


async def test_contains_is_case_insensitive_by_default() -> None:
    assert (await run("Refunds within 30 DAYS", type="contains", value="30 days")).passed
    assert not (
        await run("Refunds within 30 DAYS", type="contains", value="30 days", case_sensitive=True)
    ).passed


async def test_contains_accepts_a_list_and_names_what_is_missing() -> None:
    result = await run("apples and pears", type="contains", value=["apples", "plums"])
    assert not result.passed and "plums" in result.reason and "apples" not in result.reason


async def test_not_contains_reports_what_was_found() -> None:
    assert (await run("all good", type="not_contains", value="error")).passed
    result = await run("An ERROR occurred", type="not_contains", value="error")
    assert not result.passed and "error" in result.reason


async def test_regex() -> None:
    assert (await run("order 12345", type="regex", value=r"order \d{5}")).passed
    assert not (await run("order 12", type="regex", value=r"order \d{5}")).passed


async def test_json_valid_accepts_fenced_json_and_rejects_prose() -> None:
    assert (await run('```json\n{"a": 1}\n```', type="json_valid")).passed
    assert not (await run("here you go: {a}", type="json_valid")).passed


async def test_json_schema_passes_and_names_the_failing_path() -> None:
    schema = {
        "type": "object",
        "required": ["items"],
        "properties": {
            "items": {
                "type": "array",
                "items": {"type": "object", "properties": {"price": {"type": "number"}}},
            }
        },
    }
    assert (await run('{"items": [{"price": 1.5}]}', type="json_schema", schema=schema)).passed

    bad = await run('{"items": [{"price": "free"}]}', type="json_schema", schema=schema)
    assert not bad.passed
    assert bad.reason.startswith("$.items[0].price:")
    assert not (await run("not json", type="json_schema", schema=schema)).passed


async def test_max_latency_ms() -> None:
    assert (await run("x", latency_ms=100, type="max_latency_ms", value=200)).passed
    result = await run("x", latency_ms=300, type="max_latency_ms", value=200)
    assert not result.passed and "300ms" in result.reason


async def test_llm_judge_compares_score_with_threshold() -> None:
    seen: list[tuple[str, str, str]] = []

    async def judge(rubric: str, prompt: str, answer: str) -> tuple[float, str]:
        seen.append((rubric, prompt, answer))
        return 0.6, "meh"

    result = await run("the answer", judge=judge, type="llm_judge", rubric="be nice", threshold=0.7)
    assert (result.passed, result.score, result.reason) == (False, 0.6, "meh")
    assert seen == [("be nice", "the question", "the answer")]

    async def generous(rubric: str, prompt: str, answer: str) -> tuple[float, str]:
        return 0.9, "great"

    assert (await run("a", judge=generous, type="llm_judge", rubric="r", threshold=0.7)).passed


async def test_llm_judge_without_a_judge_fails_cleanly() -> None:
    result = await run("a", type="llm_judge", rubric="r")
    assert not result.passed and "no judge" in result.reason


def test_parse_judge_reply_handles_fences_and_chatter() -> None:
    assert parse_judge_reply('{"score": 0.8, "reason": "good"}') == (0.8, "good")
    assert parse_judge_reply('Sure!\n```json\n{"score": 1, "reason": "ok"}\n```') == (1.0, "ok")
    assert parse_judge_reply('{"score": 7, "reason": "x"}')[0] == 1.0  # clamped


def test_judge_invalid_json_scores_zero() -> None:
    assert parse_judge_reply("I think it's pretty good!") == (0.0, "judge returned invalid JSON")
    assert parse_judge_reply('{"score": "high"}') == (0.0, "judge returned invalid JSON")
    assert parse_judge_reply('{"reason": "no score"}') == (0.0, "judge returned invalid JSON")
