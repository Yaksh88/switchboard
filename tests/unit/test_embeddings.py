from __future__ import annotations

import numpy as np

from switchboard.cache.embeddings import HashingEmbedder
from switchboard.cache.replay import replay_as_chunks
from switchboard.schemas import AssistantMessage, ChatCompletionResponse, Choice, Usage


async def test_hashing_embedder_is_deterministic_and_normalised() -> None:
    embedder = HashingEmbedder()
    a = await embedder.embed("Reset my password")
    b = await embedder.embed("Reset my password")
    assert a.shape == (embedder.dim,)
    assert np.array_equal(a, b)
    assert abs(float(np.linalg.norm(a)) - 1.0) < 1e-5


async def test_hashing_embedder_similar_texts_score_higher() -> None:
    embedder = HashingEmbedder()
    base = await embedder.embed("how do I reset my password")
    near = await embedder.embed("how can I reset my password")
    far = await embedder.embed("what is the capital of France")
    assert float(base @ near) > 0.7
    assert float(base @ near) > float(base @ far) + 0.5


async def test_hashing_embedder_handles_empty_text() -> None:
    vector = await HashingEmbedder().embed("")
    assert not np.isnan(vector).any()


async def test_replay_as_chunks_reconstructs_text_and_usage() -> None:
    text = "The quick brown fox jumps over the lazy dog, twice."
    resp = ChatCompletionResponse(
        model="m",
        choices=[Choice(message=AssistantMessage(content=text), finish_reason="length")],
        usage=Usage.of(7, 11),
    )
    chunks = [c async for c in replay_as_chunks(resp)]
    assert chunks[0].choices[0].delta.role == "assistant"
    assert "".join(c.choices[0].delta.content or "" for c in chunks if c.choices) == text
    assert chunks[-1].choices[0].finish_reason == "length"
    assert chunks[-1].usage == Usage.of(7, 11)
