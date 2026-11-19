from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token); used where no tokenizer is available."""
    return max(1, len(text) // 4)
