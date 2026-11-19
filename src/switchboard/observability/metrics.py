"""Prometheus metrics, exposed at ``/metrics``."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from switchboard.context import RequestContext

REGISTRY = CollectorRegistry()

REQUESTS = Counter(
    "switchboard_requests_total",
    "Gateway requests.",
    ["model", "provider", "status", "cache"],
    registry=REGISTRY,
)
LATENCY = Histogram(
    "switchboard_request_duration_seconds",
    "End-to-end request latency.",
    ["model", "provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
    registry=REGISTRY,
)
TTFT = Histogram(
    "switchboard_time_to_first_token_seconds",
    "Time until the first streamed content chunk.",
    ["model", "provider"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
    registry=REGISTRY,
)
TOKENS = Counter(
    "switchboard_tokens_total",
    "Tokens processed.",
    ["model", "provider", "kind"],
    registry=REGISTRY,
)
COST = Counter(
    "switchboard_cost_usd_total",
    "Estimated spend in USD.",
    ["model", "provider"],
    registry=REGISTRY,
)
FALLBACKS = Counter(
    "switchboard_fallbacks_total",
    "Requests served by a target other than the first choice.",
    ["model"],
    registry=REGISTRY,
)
BREAKER_OPEN = Gauge(
    "switchboard_circuit_breaker_open",
    "1 when the target's circuit breaker is not closed.",
    ["provider", "model"],
    registry=REGISTRY,
)
LOG_DROPPED = Counter(
    "switchboard_log_dropped",
    "Request log rows dropped because the write queue was full or a flush failed.",
    registry=REGISTRY,
)


def record(ctx: RequestContext) -> None:
    provider = ctx.provider or "none"
    REQUESTS.labels(ctx.model_alias, provider, str(ctx.status_code), ctx.cache).inc()
    LATENCY.labels(ctx.model_alias, provider).observe(ctx.elapsed_ms() / 1000)
    if ctx.ttft_ms is not None:
        TTFT.labels(ctx.model_alias, provider).observe(ctx.ttft_ms / 1000)
    if ctx.usage is not None:
        TOKENS.labels(ctx.model_alias, provider, "prompt").inc(ctx.usage.prompt_tokens)
        TOKENS.labels(ctx.model_alias, provider, "completion").inc(ctx.usage.completion_tokens)
    if ctx.cost_usd:
        COST.labels(ctx.model_alias, provider).inc(ctx.cost_usd)
