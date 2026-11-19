from __future__ import annotations

from typing import Any

from switchboard.config import GatewayConfig, Target
from switchboard.context import RequestContext
from switchboard.gateway import Gateway
from switchboard.observability.cost import compute_cost
from switchboard.schemas import Usage


def priced_config() -> GatewayConfig:
    return GatewayConfig.model_validate(
        {
            "providers": {"mock": {"type": "mock"}},
            "models": {
                "m": {
                    "targets": [
                        {
                            "provider": "mock",
                            "model": "mock-1",
                            "input_cost_per_mtok": 1.0,
                            "output_cost_per_mtok": 5.0,
                        }
                    ]
                }
            },
        }
    )


def finished(**fields: Any) -> RequestContext:
    ctx = RequestContext(model_alias="m", **fields)
    Gateway(priced_config()).finish(ctx)
    return ctx


def test_compute_cost_uses_per_million_prices() -> None:
    target = Target(provider="p", model="m", input_cost_per_mtok=1.0, output_cost_per_mtok=5.0)
    assert compute_cost(Usage.of(1000, 500), target) == 0.0035


def test_unpriced_targets_cost_nothing() -> None:
    assert compute_cost(Usage.of(1000, 500), Target(provider="p", model="m")) == 0.0


def test_cost_is_charged_to_the_target_that_served_the_request() -> None:
    ctx = finished(
        provider="mock", upstream_model="mock-1", usage=Usage.of(1000, 500), cache="miss"
    )
    assert ctx.cost_usd == 0.0035


def test_cache_hit_costs_nothing() -> None:
    for status in ("exact", "semantic"):
        ctx = finished(
            provider="cache", upstream_model="mock-1", usage=Usage.of(1000, 500), cache=status
        )
        assert ctx.cost_usd == 0


def test_failed_request_without_usage_costs_nothing() -> None:
    assert finished(provider="mock", upstream_model="mock-1").cost_usd == 0
