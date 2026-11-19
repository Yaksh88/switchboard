"""Fill the database with a week of realistic demo traffic and eval runs.

    uv run python scripts/seed_demo.py            # ~5,000 requests over 7 days
    uv run python scripts/seed_demo.py --requests 20000

Safe to re-run: rows from a previous run are replaced. Uses SWITCHBOARD_DATABASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import create_async_engine

from switchboard.config import Settings
from switchboard.db.models import EvalRun, RequestLog

PREFIX = "req_demo"
SUITE = "support-bot"

# alias -> (traffic share, provider, upstream model, $/M in, $/M out, median latency ms)
MODELS: dict[str, tuple[float, str, str, float, float, float]] = {
    "gpt-4o-mini": (0.40, "openai", "gpt-4o-mini", 0.15, 0.60, 700),
    "claude-haiku": (0.30, "anthropic", "claude-haiku-4-5", 1.00, 5.00, 900),
    "resilient": (0.20, "anthropic", "claude-haiku-4-5", 1.00, 5.00, 950),
    "llama3": (0.10, "ollama", "llama3.1", 0.0, 0.0, 1400),
}
FAILURES = [
    (503, "[anthropic] upstream overloaded"),
    (500, "[openai] internal server error"),
    (429, "Rate limit of 60 requests per minute exceeded"),
    (499, "client disconnected"),
    (504, "[ollama] request timed out"),
]


def pick_time(rng: random.Random, now: datetime, days: int) -> datetime:
    """Sample a timestamp with a daily rhythm: busier in the afternoon, quiet overnight."""
    while True:
        t = now - timedelta(seconds=rng.uniform(0, days * 86_400))
        hour = t.hour + t.minute / 60
        if rng.random() < 0.55 + 0.45 * math.sin((hour - 9) / 24 * 2 * math.pi) ** 2:
            return t


def build_requests(count: int, days: int, rng: random.Random) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    incident = (now - timedelta(minutes=95), now - timedelta(minutes=55))  # anthropic degrades
    aliases = list(MODELS)
    weights = [MODELS[a][0] for a in aliases]
    rows: list[dict[str, Any]] = []
    for _ in range(count):
        alias = rng.choices(aliases, weights)[0]
        _, provider, upstream, price_in, price_out, median = MODELS[alias]
        created = pick_time(rng, now, days)
        in_incident = incident[0] <= created <= incident[1] and provider == "anthropic"
        stream = rng.random() < 0.55
        attempts, status, error = 1, 200, None
        cache = "miss"

        if rng.random() < (0.45 if in_incident else 0.03):
            status, error = rng.choice(FAILURES)
            if alias == "resilient" and status in (503, 500, 504):
                status, error, provider, upstream, attempts = 200, None, "openai", "gpt-4o-mini", 3
                price_in, price_out = 0.15, 0.60
            else:
                attempts = 2 if status >= 500 else 1
        elif rng.random() < 0.25:
            cache = "exact" if rng.random() < 0.72 else "semantic"

        prompt = int(rng.lognormvariate(math.log(350), 0.6))
        completion = int(rng.lognormvariate(math.log(180), 0.7))
        latency = rng.lognormvariate(math.log(median), 0.45) * (1 + 0.8 * (attempts - 1))
        served_by = provider
        if cache != "miss":
            served_by, latency = "cache", rng.uniform(3, 15)
        if status != 200:
            prompt = completion = 0
            latency = rng.uniform(40, 400) if status in (429, 499) else latency * 0.6
        cost = (prompt * price_in + completion * price_out) / 1e6 if cache == "miss" else 0.0

        rows.append(
            {
                "request_id": "",
                "created_at": created,
                "api_key_id": None,
                "model_alias": alias,
                "provider": served_by if status != 429 else None,
                "upstream_model": upstream if cache == "miss" and status != 429 else None,
                "status_code": status,
                "cache": cache,
                "attempts": attempts,
                "stream": stream,
                "latency_ms": round(latency, 1),
                "ttft_ms": round(latency * rng.uniform(0.2, 0.5), 1)
                if stream and status == 200
                else None,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cost_usd": round(cost, 6),
                "error": error,
            }
        )
    # Ids ascend with time, as they would in a real log.
    rows.sort(key=lambda row: row["created_at"])
    for i, row in enumerate(rows):
        row["request_id"] = f"{PREFIX}{i:08d}"
    return rows


CASES = [
    ("refund-inside-window", True, "contains", "found", "regex", "no match for /refund/"),
    (
        "refund-outside-window",
        False,
        "llm_judge",
        "explains the window and offers credit",
        "llm_judge",
        "promised a refund",
    ),
    (
        "stays-empathetic",
        False,
        "llm_judge",
        "acknowledges frustration",
        "llm_judge",
        "defensive tone",
    ),
    (
        "extracts-ticket-fields",
        False,
        "json_schema",
        "matches schema",
        "json_schema",
        "$.urgent: 'yes' is not of type 'boolean'",
    ),
    (
        "does-not-promise-roadmap",
        True,
        "llm_judge",
        "makes no commitment",
        "llm_judge",
        "committed to an SSO date",
    ),
    (
        "replies-within-budget",
        False,
        "max_latency_ms",
        "620ms <= 8000ms",
        "max_latency_ms",
        "9100ms > 8000ms",
    ),
]
# How many of the six cases pass on each run, oldest to newest: a prompt being tuned.
PASSING_OVER_TIME = [3, 3, 4, 4, 5, 5, 4, 5, 6, 6]
FAILS_FIRST = [3, 1, 5, 2, 4, 0]  # which cases fail first when fewer than six pass


def build_eval_runs(rng: random.Random) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    runs = []
    for n, passing in enumerate(PASSING_OVER_TIME):
        failing = set(FAILS_FIRST[: 6 - passing])
        cases = []
        for index, (case_id, critical, ok_kind, ok_reason, bad_kind, bad_reason) in enumerate(
            CASES
        ):
            ok = index not in failing
            cases.append(
                {
                    "case_id": case_id,
                    "passed": ok,
                    "critical": critical,
                    "output": "Thanks for reaching out. Here is what I can do for you."
                    if ok
                    else "I can't help with that.",
                    "latency_ms": round(rng.uniform(450, 1500), 1),
                    "cost_usd": round(rng.uniform(0.00005, 0.0004), 6),
                    "error": None,
                    "scores": [
                        {
                            "type": ok_kind if ok else bad_kind,
                            "passed": ok,
                            "score": 1.0 if ok else round(rng.uniform(0.1, 0.5), 2),
                            "reason": ok_reason if ok else bad_reason,
                        }
                    ],
                }
            )
        started = now - timedelta(days=5) + timedelta(hours=12 * n)
        duration = round(rng.uniform(6, 14), 1)
        runs.append(
            {
                "id": uuid.uuid4(),
                "suite": SUITE,
                "model": "gpt-4o-mini",
                "created_at": started,
                "pass_rate": passing / 6,
                "duration_s": duration,
                "total_cases": 6,
                "passed_cases": passing,
                "result": {
                    "suite": SUITE,
                    "model": "gpt-4o-mini",
                    "started_at": started.isoformat(),
                    "duration_s": duration,
                    "pass_rate": passing / 6,
                    "total_cases": 6,
                    "passed_cases": passing,
                    "cases": cases,
                },
            }
        )
    for n in range(6):  # the smoke suite: steady, with one bad day
        passed = 2 if n == 3 else 3
        started = now - timedelta(days=5) + timedelta(hours=20 * n)
        runs.append(
            {
                "id": uuid.uuid4(),
                "suite": "smoke",
                "model": "mock",
                "created_at": started,
                "pass_rate": passed / 3,
                "duration_s": 0.9,
                "total_cases": 3,
                "passed_cases": passed,
                "result": {
                    "suite": "smoke",
                    "model": "mock",
                    "started_at": started.isoformat(),
                    "duration_s": 0.9,
                    "pass_rate": passed / 3,
                    "total_cases": 3,
                    "passed_cases": passed,
                    "cases": [
                        {
                            "case_id": cid,
                            "passed": not (n == 3 and cid == "replies-quickly"),
                            "critical": cid == "echoes-the-prompt",
                            "output": "echo: ping",
                            "latency_ms": 2600.0
                            if (n == 3 and cid == "replies-quickly")
                            else 180.0,
                            "cost_usd": 0.0,
                            "error": None,
                            "scores": [],
                        }
                        for cid in (
                            "echoes-the-prompt",
                            "follows-the-conversation",
                            "replies-quickly",
                        )
                    ],
                },
            }
        )
    return runs


async def main(requests: int, days: int, seed: int) -> None:
    url = Settings().database_url
    if not url:
        raise SystemExit("SWITCHBOARD_DATABASE_URL is not set")
    rng = random.Random(seed)
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.execute(delete(RequestLog).where(RequestLog.request_id.like(f"{PREFIX}%")))
        await conn.execute(delete(EvalRun).where(EvalRun.suite.in_([SUITE, "smoke"])))
        rows = build_requests(requests, days, rng)
        for start in range(0, len(rows), 1000):
            await conn.execute(insert(RequestLog), rows[start : start + 1000])
        runs = build_eval_runs(rng)
        await conn.execute(insert(EvalRun), runs)
    await engine.dispose()
    print(f"Seeded {len(rows)} requests over {days} days and {len(runs)} eval runs.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--requests", type=int, default=5000)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    asyncio.run(main(args.requests, args.days, args.seed))
