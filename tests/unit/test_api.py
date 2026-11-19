from __future__ import annotations

import json
from collections.abc import Callable

from fastapi.testclient import TestClient


def test_chat_completion_returns_openai_shape_with_alias(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [{"role": "user", "content": "ping"}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "mock"
    assert body["choices"][0]["message"]["content"] == "echo: ping"
    assert body["usage"]["total_tokens"] > 0


def test_streaming_emits_sse_chunks_and_done(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={"model": "mock", "stream": True, "messages": [{"role": "user", "content": "a b"}]},
    ) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = [line[6:] for line in resp.iter_lines() if line.startswith("data: ")]

    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
    assert text.strip() == "echo: a b"
    assert chunks[-1]["usage"]["completion_tokens"] > 0


def test_unknown_model_is_404_in_openai_error_format(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "nope", "messages": [{"role": "user", "content": "x"}]},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


def test_validation_errors_are_400(client: TestClient) -> None:
    resp = client.post("/v1/chat/completions", json={"model": "mock", "messages": []})
    assert resp.status_code == 400
    assert resp.json()["error"]["type"] == "invalid_request_error"


def test_models_endpoint_lists_aliases(client: TestClient) -> None:
    assert [m["id"] for m in client.get("/v1/models").json()["data"]] == ["mock"]


def test_response_includes_routing_headers(client: TestClient) -> None:
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [{"role": "user", "content": "ping"}]},
    )
    assert resp.headers["x-request-id"].startswith("req_")
    assert resp.headers["x-switchboard-provider"] == "mock"
    assert resp.headers["x-switchboard-cache"] == "bypass"
    assert resp.headers["x-switchboard-attempts"] == "1"


def test_response_reports_cost_of_the_request(make_client: Callable[..., TestClient]) -> None:
    client = make_client(
        models={
            "mock": {
                "targets": [
                    {
                        "provider": "mock",
                        "model": "mock-1",
                        "input_cost_per_mtok": 1_000_000.0,
                        "output_cost_per_mtok": 0.0,
                    }
                ]
            }
        }
    )
    resp = client.post(
        "/v1/chat/completions",
        json={"model": "mock", "messages": [{"role": "user", "content": "ping"}]},
    )
    prompt_tokens = resp.json()["usage"]["prompt_tokens"]
    assert float(resp.headers["x-switchboard-cost-usd"]) == prompt_tokens
