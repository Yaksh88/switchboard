from __future__ import annotations

import hashlib
import itertools
import re
from typing import Protocol

import httpx
import numpy as np
from numpy.typing import NDArray

from switchboard.errors import ProviderError

Vector = NDArray[np.float32]

_WORD = re.compile(r"\w+")


class Embedder(Protocol):
    dim: int

    async def embed(self, text: str) -> Vector:
        """Return an L2-normalised embedding."""
        ...


def _normalise(vector: Vector) -> Vector:
    norm = float(np.linalg.norm(vector))
    return vector if norm == 0 else (vector / norm).astype(np.float32)


class HashingEmbedder:
    """Local bag-of-words embedder (hashing trick over word unigrams and bigrams).

    Needs no model or API call, so it adds no cost or latency. It matches rewordings
    that share most of their words, not true paraphrases; use ``OpenAIEmbedder`` for those.
    """

    dim = 512

    async def embed(self, text: str) -> Vector:
        words = _WORD.findall(text.lower())
        features = words + [f"{a} {b}" for a, b in itertools.pairwise(words)]
        vector = np.zeros(self.dim, dtype=np.float32)
        for feature in features:
            h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
            vector[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
        return _normalise(vector)


class OpenAIEmbedder:
    """Calls ``POST /embeddings`` on an OpenAI-compatible endpoint."""

    dim = 0  # depends on the model; unused by SemanticCache

    def __init__(self, client: httpx.AsyncClient, model: str, provider_name: str) -> None:
        self.client = client
        self.model = model
        self.provider_name = provider_name

    async def embed(self, text: str) -> Vector:
        try:
            response = await self.client.post(
                "/embeddings", json={"model": self.model, "input": text}
            )
            response.raise_for_status()
            data = response.json()["data"][0]["embedding"]
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise ProviderError(self.provider_name, f"embedding failed: {exc}") from exc
        return _normalise(np.asarray(data, dtype=np.float32))
