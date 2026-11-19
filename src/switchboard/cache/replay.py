from __future__ import annotations

from collections.abc import AsyncIterator

from switchboard.schemas import (
    ChatCompletionChunk,
    ChatCompletionResponse,
    ChunkChoice,
    Delta,
    new_completion_id,
)

CHUNK_CHARS = 20


async def replay_as_chunks(resp: ChatCompletionResponse) -> AsyncIterator[ChatCompletionChunk]:
    """Re-emit a stored completion as an OpenAI-style chunk stream."""
    chunk_id = new_completion_id()

    yield ChatCompletionChunk(
        id=chunk_id, model=resp.model, choices=[ChunkChoice(delta=Delta(role="assistant"))]
    )
    text = resp.text()
    for start in range(0, len(text), CHUNK_CHARS):
        yield ChatCompletionChunk(
            id=chunk_id,
            model=resp.model,
            choices=[ChunkChoice(delta=Delta(content=text[start : start + CHUNK_CHARS]))],
        )
    finish = resp.choices[0].finish_reason if resp.choices else "stop"
    yield ChatCompletionChunk(
        id=chunk_id,
        model=resp.model,
        choices=[ChunkChoice(delta=Delta(), finish_reason=finish or "stop")],
        usage=resp.usage,
    )
