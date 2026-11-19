from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator

from switchboard.errors import ProviderError
from switchboard.providers.base import Provider
from switchboard.schemas import (
    AssistantMessage,
    ChatCompletionChunk,
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChunkChoice,
    Delta,
    Usage,
    new_completion_id,
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token), good enough for a fake provider."""
    return max(1, len(text) // 4)


class MockProvider(Provider):
    """Deterministic in-process provider.

    Echoes the last user message. ``latency_ms`` and ``failure_rate`` let you
    exercise fallback, circuit breaking and timeouts without real API keys.
    """

    async def _simulate(self) -> None:
        if self.config.latency_ms:
            await asyncio.sleep(self.config.latency_ms / 1000)
        if self.config.failure_rate and random.random() < self.config.failure_rate:
            raise ProviderError(self.name, "simulated upstream failure", status_code=503)

    def _reply(self, req: ChatCompletionRequest) -> tuple[str, int]:
        last_user = next((m.text() for m in reversed(req.messages) if m.role == "user"), "")
        prompt_tokens = sum(estimate_tokens(m.text()) for m in req.messages)
        return f"echo: {last_user}", prompt_tokens

    async def complete(self, req: ChatCompletionRequest, model: str) -> ChatCompletionResponse:
        await self._simulate()
        text, prompt_tokens = self._reply(req)
        return ChatCompletionResponse(
            model=model,
            choices=[Choice(message=AssistantMessage(content=text))],
            usage=Usage.of(prompt_tokens, estimate_tokens(text)),
        )

    async def stream(
        self, req: ChatCompletionRequest, model: str
    ) -> AsyncIterator[ChatCompletionChunk]:
        await self._simulate()
        text, prompt_tokens = self._reply(req)
        chunk_id = new_completion_id()
        yield ChatCompletionChunk(
            id=chunk_id, model=model, choices=[ChunkChoice(delta=Delta(role="assistant"))]
        )
        for word in text.split(" "):
            yield ChatCompletionChunk(
                id=chunk_id, model=model, choices=[ChunkChoice(delta=Delta(content=word + " "))]
            )
        yield ChatCompletionChunk(
            id=chunk_id,
            model=model,
            choices=[ChunkChoice(delta=Delta(), finish_reason="stop")],
            usage=Usage.of(prompt_tokens, estimate_tokens(text)),
        )
