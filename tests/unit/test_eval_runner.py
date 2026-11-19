from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from switchboard.evals.runner import EvalRunner, RunResult
from switchboard.evals.suite import Suite


def suite(**overrides: Any) -> Suite:
    raw: dict[str, Any] = {
        "name": "s",
        "model": "default-model",
        "system": "be nice",
        "defaults": {"temperature": 0, "max_tokens": 64},
        "cases": [
            {"id": f"c{i}", "input": f"q{i}", "assert": [{"type": "contains", "value": f"q{i}"}]}
            for i in range(3)
        ],
    }
    raw.update(overrides)
    return Suite.model_validate(raw)


def completion(text: str, cost: str | None = "0.000100") -> httpx.Response:
    headers = {"x-switchboard-cost-usd": cost} if cost else {}
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        },
        headers=headers,
    )


def runner_for(handler: Any, **kwargs: Any) -> EvalRunner:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gw")
    return EvalRunner("http://gw", client=client, **kwargs)


def echo(request: httpx.Request) -> httpx.Response:
    messages = json.loads(request.content)["messages"]
    return completion(f"echo {messages[-1]['content']}")


async def test_runs_every_case_and_records_output_cost_and_latency() -> None:
    async with runner_for(echo) as runner:
        result = await runner.run(suite())

    assert [c.case_id for c in result.cases] == ["c0", "c1", "c2"]
    assert result.pass_rate == 1.0
    first = result.cases[0]
    assert first.output == "echo q0" and first.cost_usd == 0.0001 and first.latency_ms >= 0
    assert first.scores[0][0] == "contains" and first.scores[0][1].passed


async def test_sends_model_override_system_prompt_and_defaults() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return completion("q0 q1 q2")

    async with runner_for(handler) as runner:
        await runner.run(suite(), model="override")

    body = sent[0]
    assert body["model"] == "override"
    assert (body["temperature"], body["max_tokens"]) == (0, 64)
    assert body["messages"][0] == {"role": "system", "content": "be nice"}


async def test_sends_the_api_key_as_a_bearer_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        return completion("q0 q1 q2")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gw")
    async with EvalRunner("http://gw", "sb-secret", client=client) as runner:
        await runner.run(suite())
    assert set(seen) == {"Bearer sb-secret"}


async def test_respects_the_concurrency_limit() -> None:
    in_flight = peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return completion("q0 q1 q2 q3 q4 q5")

    cases = [
        {"id": f"c{i}", "input": "x", "assert": [{"type": "contains", "value": "q0"}]}
        for i in range(6)
    ]
    async with runner_for(handler, concurrency=2) as runner:
        result = await runner.run(suite(cases=cases))

    assert len(result.cases) == 6
    assert peak == 2


async def test_http_errors_fail_the_case_without_aborting_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.loads(request.content)["messages"][-1]["content"]
        if content == "q1":
            return httpx.Response(503, json={"error": {"message": "upstream down"}})
        if content == "q2":
            raise httpx.ConnectError("refused")
        return completion("q0")

    async with runner_for(handler) as runner:
        result = await runner.run(suite())

    ok, unavailable, refused = result.cases
    assert ok.passed
    assert not unavailable.passed and "HTTP 503" in (unavailable.error or "")
    assert not refused.passed and refused.error
    assert result.pass_rate == 1 / 3


async def test_malformed_gateway_responses_fail_the_case() -> None:
    async with runner_for(lambda request: httpx.Response(200, json={"oops": True})) as runner:
        result = await runner.run(suite())
    assert all(not c.passed and "unexpected response" in (c.error or "") for c in result.cases)


async def test_failed_assertions_are_reported_with_reasons() -> None:
    async with runner_for(lambda request: completion("nothing relevant")) as runner:
        result = await runner.run(suite())
    case = result.cases[0]
    assert not case.passed
    assert "missing" in case.scores[0][1].reason


async def test_llm_judge_calls_the_gateway_with_the_judge_model() -> None:
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        models.append(body["model"])
        if "grading an AI assistant" in body["messages"][-1]["content"]:
            return completion('{"score": 0.9, "reason": "good"}')
        return completion("a fine answer")

    cases = [
        {
            "id": "judged",
            "input": "hello",
            "assert": [{"type": "llm_judge", "rubric": "be good", "judge_model": "judge-model"}],
        }
    ]
    async with runner_for(handler) as runner:
        result = await runner.run(suite(cases=cases))

    (case,) = result.cases
    assert case.passed and case.scores[0][1].score == 0.9
    assert models == ["default-model", "judge-model"]


async def test_failing_judge_call_fails_the_assertion_not_the_run() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "grading an AI assistant" in json.loads(request.content)["messages"][-1]["content"]:
            return httpx.Response(500, text="boom")
        return completion("answer")

    cases = [
        {"id": "j", "input": "hi", "assert": [{"type": "llm_judge", "rubric": "r"}]},
        {"id": "k", "input": "hi", "assert": [{"type": "contains", "value": "answer"}]},
    ]
    async with runner_for(handler) as runner:
        result = await runner.run(suite(cases=cases))

    judged, plain = result.cases
    assert not judged.passed and "judge call failed" in judged.scores[0][1].reason
    assert plain.passed


async def test_results_round_trip_through_json() -> None:
    async with runner_for(echo) as runner:
        result = await runner.run(suite())

    data = json.loads(json.dumps(result.to_json()))
    assert (data["total_cases"], data["passed_cases"], data["pass_rate"]) == (3, 3, 1.0)
    restored = RunResult.from_json(data)
    assert restored.to_json() == result.to_json()
