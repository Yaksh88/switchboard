"""Cache key derivation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from switchboard.schemas import ChatCompletionRequest

EXACT_PREFIX = "sb:cache:exact:"
SEMANTIC_PREFIX = "sb:cache:sem:"


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def is_cacheable(req: ChatCompletionRequest) -> bool:
    """Only deterministic requests are safe to replay from cache."""
    return req.temperature in (None, 0)


def exact_key(req: ChatCompletionRequest) -> str:
    # ``stream`` and ``user`` don't change the answer, so they are left out of the key.
    return EXACT_PREFIX + _digest(
        {
            "model": req.model,
            "messages": [m.model_dump(exclude_none=True) for m in req.messages],
            "temperature": req.temperature,
            "top_p": req.top_p,
            "max_tokens": req.max_tokens,
            "stop": req.stop_list(),
        }
    )


def semantic_namespace(req: ChatCompletionRequest) -> str:
    """Scope semantic lookups to requests sharing the same model and conversation context.

    Only the final message is compared by embedding, so everything before it (system
    prompt, earlier turns) must match exactly.
    """
    context = _digest(
        {
            "messages": [m.model_dump(exclude_none=True) for m in req.messages[:-1]],
            "temperature": req.temperature,
            "top_p": req.top_p,
            "max_tokens": req.max_tokens,
            "stop": req.stop_list(),
        }
    )
    return f"{SEMANTIC_PREFIX}{req.model}:{context}"


def last_user_text(req: ChatCompletionRequest) -> str:
    for message in reversed(req.messages):
        if message.role == "user":
            return message.text()
    return req.messages[-1].text()
