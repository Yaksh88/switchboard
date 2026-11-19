from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Self

import httpx

from switchboard.evals.scorers import (
    Completion,
    JudgeFn,
    ScoreResult,
    parse_judge_reply,
    score,
)
from switchboard.evals.suite import EvalCase, Suite
from switchboard.schemas import Usage

JUDGE_PROMPT = """You are grading an AI assistant's answer against a rubric.

Rubric: {rubric}

Question: {prompt}

Answer: {answer}

Reply with ONLY a JSON object: {{"score": <number from 0 to 1>, "reason": "<one sentence>"}}"""

MAX_ERROR_CHARS = 300


class EvalCallError(Exception):
    """The gateway returned something other than a usable completion."""


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    critical: bool
    output: str
    latency_ms: float
    cost_usd: float | None
    scores: list[tuple[str, ScoreResult]] = field(default_factory=list)
    error: str | None = None


@dataclass
class RunResult:
    suite: str
    model: str
    started_at: datetime
    duration_s: float
    cases: list[CaseResult]

    @property
    def pass_rate(self) -> float:
        return sum(c.passed for c in self.cases) / len(self.cases) if self.cases else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "model": self.model,
            "started_at": self.started_at.isoformat(),
            "duration_s": self.duration_s,
            "pass_rate": self.pass_rate,
            "total_cases": len(self.cases),
            "passed_cases": sum(c.passed for c in self.cases),
            "cases": [
                {
                    "case_id": c.case_id,
                    "passed": c.passed,
                    "critical": c.critical,
                    "output": c.output,
                    "latency_ms": c.latency_ms,
                    "cost_usd": c.cost_usd,
                    "error": c.error,
                    "scores": [
                        {"type": kind, "passed": s.passed, "score": s.score, "reason": s.reason}
                        for kind, s in c.scores
                    ],
                }
                for c in self.cases
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Self:
        return cls(
            suite=data["suite"],
            model=data["model"],
            started_at=datetime.fromisoformat(data["started_at"]),
            duration_s=data["duration_s"],
            cases=[
                CaseResult(
                    case_id=c["case_id"],
                    passed=c["passed"],
                    critical=c["critical"],
                    output=c["output"],
                    latency_ms=c["latency_ms"],
                    cost_usd=c["cost_usd"],
                    error=c["error"],
                    scores=[
                        (s["type"], ScoreResult(s["passed"], s["score"], s["reason"]))
                        for s in c["scores"]
                    ],
                )
                for c in data["cases"]
            ],
        )


class EvalRunner:
    """Runs a suite against a Switchboard gateway over HTTP, ``concurrency`` cases at a time."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        *,
        concurrency: int = 8,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=120)
        self._headers = {"authorization": f"Bearer {api_key}"} if api_key else {}
        self.concurrency = concurrency

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def run(self, suite: Suite, model: str | None = None) -> RunResult:
        chosen = model or suite.model
        started_at = datetime.now(UTC)
        started = time.perf_counter()
        slots = asyncio.Semaphore(self.concurrency)
        cases = await asyncio.gather(
            *(self._run_case(suite, c, chosen, slots) for c in suite.cases)
        )
        return RunResult(suite.name, chosen, started_at, time.perf_counter() - started, list(cases))

    async def _chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float | None,
        max_tokens: int | None,
    ) -> tuple[Completion, float | None]:
        payload: dict[str, Any] = {"model": model, "messages": messages}
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        started = time.perf_counter()
        response = await self.client.post(
            "/v1/chat/completions", json=payload, headers=self._headers
        )
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise EvalCallError(f"HTTP {response.status_code}: {response.text[:MAX_ERROR_CHARS]}")
        try:
            body = response.json()
            text = body["choices"][0]["message"]["content"] or ""
            usage = Usage.model_validate(body.get("usage") or {})
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise EvalCallError(f"unexpected response shape: {exc}") from exc
        cost = response.headers.get("x-switchboard-cost-usd")
        return Completion(text, latency_ms, usage), float(cost) if cost is not None else None

    def _judge(self, model: str) -> JudgeFn:
        async def judge(rubric: str, prompt: str, answer: str) -> tuple[float, str]:
            completion, _ = await self._chat(
                model,
                [
                    {
                        "role": "user",
                        "content": JUDGE_PROMPT.format(rubric=rubric, prompt=prompt, answer=answer),
                    }
                ],
                temperature=0,
                max_tokens=300,
            )
            return parse_judge_reply(completion.text)

        return judge

    async def _run_case(
        self, suite: Suite, case: EvalCase, model: str, slots: asyncio.Semaphore
    ) -> CaseResult:
        async with slots:
            try:
                completion, cost = await self._chat(
                    model,
                    suite.messages_for(case),
                    suite.defaults.temperature,
                    suite.defaults.max_tokens,
                )
            except (httpx.HTTPError, EvalCallError) as exc:
                return CaseResult(case.id, False, case.critical, "", 0.0, None, error=str(exc))

            scores: list[tuple[str, ScoreResult]] = []
            for assertion in case.assertions:
                judge = (
                    self._judge(assertion.judge_model or model)
                    if assertion.type == "llm_judge"
                    else None
                )
                try:
                    result = await score(assertion, case, completion, judge)
                except (httpx.HTTPError, EvalCallError) as exc:
                    result = ScoreResult(False, 0.0, f"judge call failed: {exc}")
                scores.append((assertion.type, result))

        return CaseResult(
            case.id,
            all(s.passed for _, s in scores),
            case.critical,
            completion.text,
            completion.latency_ms,
            cost,
            scores,
        )
