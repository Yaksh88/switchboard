"""Per-request state collected as a request moves through the gateway."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from switchboard.auth.keys import Principal
from switchboard.schemas import Usage

CacheStatus = Literal["miss", "exact", "semantic", "bypass"]


@dataclass
class RequestContext:
    model_alias: str
    stream: bool = False
    request_id: str = field(default_factory=lambda: f"req_{uuid4().hex[:20]}")
    started_at: float = field(default_factory=time.perf_counter)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    principal: Principal | None = None
    api_key_id: UUID | None = None
    ratelimit_remaining: int | None = None
    provider: str | None = None
    upstream_model: str | None = None
    attempts: int = 0
    cache: CacheStatus = "bypass"
    bypass_cache: bool = False
    usage: Usage | None = None
    status_code: int = 200
    error: str | None = None
    ttft_ms: float | None = None
    cost_usd: float = 0.0

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000

    def headers(self) -> dict[str, str]:
        headers = {
            "x-request-id": self.request_id,
            "x-switchboard-cache": self.cache,
            "x-switchboard-attempts": str(self.attempts),
        }
        if self.provider:
            headers["x-switchboard-provider"] = self.provider
        if self.ratelimit_remaining is not None:
            headers["x-ratelimit-remaining-requests"] = str(self.ratelimit_remaining)
        return headers
