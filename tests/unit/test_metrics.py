from __future__ import annotations

import json
import logging
from collections.abc import Callable

from fastapi.testclient import TestClient

from switchboard.context import RequestContext
from switchboard.observability.logging import JsonFormatter
from switchboard.observability.metrics import REGISTRY, record
from switchboard.schemas import Usage


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_record_increments_counters() -> None:
    labels = {"model": "m-rec", "provider": "p-rec"}
    requests = {**labels, "status": "200", "cache": "bypass"}
    before = (
        sample("switchboard_requests_total", **requests),
        sample("switchboard_tokens_total", **labels, kind="prompt"),
        sample("switchboard_tokens_total", **labels, kind="completion"),
        sample("switchboard_cost_usd_total", **labels),
    )

    ctx = RequestContext(model_alias="m-rec", provider="p-rec", usage=Usage.of(10, 4))
    ctx.cost_usd = 0.25
    record(ctx)

    assert sample("switchboard_requests_total", **requests) == before[0] + 1
    assert sample("switchboard_tokens_total", **labels, kind="prompt") == before[1] + 10
    assert sample("switchboard_tokens_total", **labels, kind="completion") == before[2] + 4
    assert sample("switchboard_cost_usd_total", **labels) == before[3] + 0.25
    assert sample("switchboard_request_duration_seconds_count", **labels) >= 1


def test_json_formatter_emits_one_object_with_structured_fields() -> None:
    record_ = logging.LogRecord(
        "switchboard.access", logging.INFO, __file__, 1, "request", (), None
    )
    record_.fields = {"request_id": "req_1", "status": 200}  # type: ignore[attr-defined]
    line = JsonFormatter().format(record_)

    payload = json.loads(line)
    assert "\n" not in line
    assert payload["message"] == "request"
    assert payload["level"] == "info"
    assert payload["request_id"] == "req_1"
    assert payload["status"] == 200


def test_fallbacks_and_breaker_state_are_exported(make_client: Callable[..., TestClient]) -> None:
    client = make_client(
        providers={
            "bad": {"type": "mock", "failure_rate": 1.0, "max_retries": 0},
            "good": {"type": "mock"},
        },
        models={
            "fb": {
                "targets": [
                    {"provider": "bad", "model": "b-1"},
                    {"provider": "good", "model": "g-1"},
                ]
            }
        },
    )
    before = sample("switchboard_fallbacks_total", model="fb")
    body = {"model": "fb", "messages": [{"role": "user", "content": "hi"}]}
    resp = client.post("/v1/chat/completions", json=body)

    assert resp.status_code == 200
    assert resp.headers["x-switchboard-provider"] == "good"
    assert sample("switchboard_fallbacks_total", model="fb") == before + 1

    text = client.get("/metrics").text
    assert "switchboard_requests_total" in text
    assert 'switchboard_circuit_breaker_open{model="b-1",provider="bad"} 0.0' in text
