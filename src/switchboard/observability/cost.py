from __future__ import annotations

from switchboard.config import Target
from switchboard.schemas import Usage


def compute_cost(usage: Usage, target: Target) -> float:
    """USD cost of a completion, from the target's per-million-token prices."""
    cost = (
        usage.prompt_tokens * target.input_cost_per_mtok
        + usage.completion_tokens * target.output_cost_per_mtok
    ) / 1_000_000
    return round(cost, 6)
