from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from switchboard.providers.base import HttpProvider
from switchboard.schemas import ChatCompletionChunk, ChatCompletionRequest, ChatCompletionResponse


class OpenAIProvider(HttpProvider):
    """OpenAI and any OpenAI-compatible server (vLLM, Ollama, Together, Groq, ...)."""

    default_base_url = "https://api.openai.com/v1"

    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}

    def _payload(self, req: ChatCompletionRequest, model: str, *, stream: bool) -> dict[str, Any]:
        payload = req.model_dump(exclude_none=True)
        payload["model"] = model
        payload["stream"] = stream
        if stream:
            payload["stream_options"] = {"include_usage": True}
        return payload

    async def complete(self, req: ChatCompletionRequest, model: str) -> ChatCompletionResponse:
        try:
            response = await self.client.post(
                "/chat/completions", json=self._payload(req, model, stream=False)
            )
        except httpx.HTTPError as exc:
            raise self.map_transport_error(exc) from exc
        self.raise_for_status(response)
        return ChatCompletionResponse.model_validate(response.json())

    async def stream(
        self, req: ChatCompletionRequest, model: str
    ) -> AsyncIterator[ChatCompletionChunk]:
        try:
            async with self.client.stream(
                "POST", "/chat/completions", json=self._payload(req, model, stream=True)
            ) as response:
                if not response.is_success:
                    await response.aread()
                    self.raise_for_status(response)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    yield ChatCompletionChunk.model_validate(json.loads(data))
        except httpx.HTTPError as exc:
            raise self.map_transport_error(exc) from exc
