from __future__ import annotations

import json

import httpx
import pytest
import respx

from switchboard.config import ProviderConfig
from switchboard.errors import ProviderError, ProviderTimeoutError
from switchboard.providers import AnthropicProvider, OpenAIProvider
from switchboard.schemas import ChatCompletionRequest


def _req(**kw: object) -> ChatCompletionRequest:
    base: dict[str, object] = {
        "model": "alias",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
            {"role": "user", "content": "there"},
        ],
    }
    base.update(kw)
    return ChatCompletionRequest.model_validate(base)


def _sse(*events: dict[str, object] | str) -> bytes:
    lines = [f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events]
    return "".join(lines).encode()


@respx.mock
async def test_anthropic_request_translation_and_response_mapping() -> None:
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        json={
            "model": "claude-x",
            "content": [{"type": "text", "text": "hello"}],
            "stop_reason": "max_tokens",
            "usage": {"input_tokens": 12, "output_tokens": 3},
        }
    )
    provider = AnthropicProvider("anthropic", ProviderConfig(type="anthropic", api_key="k"))
    resp = await provider.complete(_req(temperature=1.7, stop="END"), "claude-x")

    sent = json.loads(route.calls.last.request.content)
    assert sent["system"] == "be terse"
    assert sent["messages"] == [{"role": "user", "content": "hi\n\nthere"}]
    assert sent["temperature"] == 1.0
    assert sent["stop_sequences"] == ["END"]
    assert sent["max_tokens"] == 1024
    assert route.calls.last.request.headers["x-api-key"] == "k"

    assert resp.text() == "hello"
    assert resp.choices[0].finish_reason == "length"
    assert resp.usage.total_tokens == 15


@respx.mock
async def test_anthropic_stream_emits_openai_chunks_with_final_usage() -> None:
    respx.post("https://api.anthropic.com/v1/messages").respond(
        content=_sse(
            {"type": "message_start", "message": {"usage": {"input_tokens": 7}}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hel"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 2},
            },
            {"type": "message_stop"},
        ),
        headers={"content-type": "text/event-stream"},
    )
    provider = AnthropicProvider("anthropic", ProviderConfig(type="anthropic", api_key="k"))
    chunks = [c async for c in provider.stream(_req(stream=True), "claude-x")]

    text = "".join(c.choices[0].delta.content or "" for c in chunks if c.choices)
    assert text == "Hello"
    assert chunks[-1].choices[0].finish_reason == "stop"
    assert chunks[-1].usage is not None and chunks[-1].usage.total_tokens == 9


@respx.mock
async def test_openai_stream_parses_sse_until_done() -> None:
    route = respx.post("https://api.openai.com/v1/chat/completions").respond(
        content=_sse(
            {"id": "c1", "model": "gpt", "choices": [{"index": 0, "delta": {"content": "a"}}]},
            {"id": "c1", "model": "gpt", "choices": [{"index": 0, "delta": {"content": "b"}}]},
            {
                "id": "c1",
                "model": "gpt",
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
            },
            "[DONE]",
        ),
    )
    provider = OpenAIProvider("openai", ProviderConfig(type="openai", api_key="k"))
    chunks = [c async for c in provider.stream(_req(stream=True), "gpt-4o-mini")]

    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "gpt-4o-mini"
    assert sent["stream_options"] == {"include_usage": True}
    assert [c.choices[0].delta.content for c in chunks[:2]] == ["a", "b"]
    assert chunks[-1].usage is not None and chunks[-1].usage.total_tokens == 3


@pytest.mark.parametrize(
    ("status", "retryable"), [(400, False), (401, False), (429, True), (503, True)]
)
@respx.mock
async def test_upstream_errors_are_classified(status: int, retryable: bool) -> None:
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        status, json={"error": {"message": "nope"}}
    )
    provider = OpenAIProvider("openai", ProviderConfig(type="openai", api_key="k"))
    with pytest.raises(ProviderError) as info:
        await provider.complete(_req(), "gpt")
    assert info.value.status_code == status
    assert info.value.retryable is retryable
    assert "nope" in info.value.message


@respx.mock
async def test_timeouts_map_to_retryable_504() -> None:
    respx.post("https://api.openai.com/v1/chat/completions").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    provider = OpenAIProvider("openai", ProviderConfig(type="openai", api_key="k"))
    with pytest.raises(ProviderTimeoutError) as info:
        await provider.complete(_req(), "gpt")
    assert info.value.status_code == 504 and info.value.retryable
