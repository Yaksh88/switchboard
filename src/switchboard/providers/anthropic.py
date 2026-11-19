from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from switchboard.errors import ProviderError
from switchboard.providers.base import HttpProvider
from switchboard.schemas import (
    AssistantMessage,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChunkChoice,
    Delta,
    FinishReason,
    Usage,
    new_completion_id,
)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024

_STOP_REASONS: dict[str, FinishReason] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}


class AnthropicProvider(HttpProvider):
    """Translates OpenAI chat requests to the Anthropic Messages API and back."""

    default_base_url = "https://api.anthropic.com/v1"

    def auth_headers(self) -> dict[str, str]:
        return {"x-api-key": self.config.api_key or "", "anthropic-version": ANTHROPIC_VERSION}

    def _payload(self, req: ChatCompletionRequest, model: str, *, stream: bool) -> dict[str, Any]:
        # Anthropic takes system prompts as a top-level field and only user/assistant turns
        # in ``messages``; consecutive same-role turns must be merged.
        system_parts: list[str] = []
        messages: list[dict[str, str]] = []
        for msg in req.messages:
            if msg.role in ("system", "developer"):
                system_parts.append(msg.text())
                continue
            role = "assistant" if msg.role == "assistant" else "user"
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += "\n\n" + msg.text()
            else:
                messages.append({"role": role, "content": msg.text()})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": req.max_tokens or DEFAULT_MAX_TOKENS,
            "stream": stream,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if req.temperature is not None:
            # OpenAI's range is [0, 2]; Anthropic's is [0, 1].
            payload["temperature"] = min(req.temperature, 1.0)
        if req.top_p is not None:
            payload["top_p"] = req.top_p
        if stops := req.stop_list():
            payload["stop_sequences"] = stops
        return payload

    async def complete(self, req: ChatCompletionRequest, model: str) -> ChatCompletionResponse:
        try:
            response = await self.client.post(
                "/messages", json=self._payload(req, model, stream=False)
            )
        except httpx.HTTPError as exc:
            raise self.map_transport_error(exc) from exc
        self.raise_for_status(response)
        body = response.json()
        text = "".join(b.get("text", "") for b in body.get("content", []) if b["type"] == "text")
        usage = body.get("usage", {})
        return ChatCompletionResponse(
            model=body.get("model", model),
            choices=[
                Choice(
                    message=AssistantMessage(content=text),
                    finish_reason=_STOP_REASONS.get(body.get("stop_reason") or "", "stop"),
                )
            ],
            usage=Usage.of(usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
        )

    async def stream(
        self, req: ChatCompletionRequest, model: str
    ) -> AsyncIterator[ChatCompletionChunk]:
        chunk_id = new_completion_id()
        input_tokens = output_tokens = 0
        finish: FinishReason | None = None
        try:
            async with self.client.stream(
                "POST", "/messages", json=self._payload(req, model, stream=True)
            ) as response:
                if not response.is_success:
                    await response.aread()
                    self.raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:].strip())
                    match event["type"]:
                        case "message_start":
                            input_tokens = event["message"]["usage"].get("input_tokens", 0)
                            yield ChatCompletionChunk(
                                id=chunk_id,
                                model=model,
                                choices=[ChunkChoice(delta=Delta(role="assistant", content=""))],
                            )
                        case "content_block_delta" if event["delta"].get("type") == "text_delta":
                            yield ChatCompletionChunk(
                                id=chunk_id,
                                model=model,
                                choices=[ChunkChoice(delta=Delta(content=event["delta"]["text"]))],
                            )
                        case "message_delta":
                            output_tokens = event.get("usage", {}).get("output_tokens", 0)
                            reason = event["delta"].get("stop_reason") or ""
                            finish = _STOP_REASONS.get(reason, "stop")
                        case "error":
                            raise self.stream_error(event)
        except httpx.HTTPError as exc:
            raise self.map_transport_error(exc) from exc

        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[ChunkChoice(delta=Delta(), finish_reason=finish or "stop")],
            usage=Usage.of(input_tokens, output_tokens),
        )

    def stream_error(self, event: dict[str, Any]) -> ProviderError:
        err = event.get("error", {})
        overloaded = err.get("type") == "overloaded_error"
        return ProviderError(
            self.name, err.get("message", "stream error"), status_code=529 if overloaded else 502
        )
