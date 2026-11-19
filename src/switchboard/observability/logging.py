from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from switchboard.context import RequestContext

access_log = logging.getLogger("switchboard.access")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with structured fields passed via ``extra={"fields": ...}``."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    """Send the ``switchboard`` logger tree to stdout as JSON lines (idempotent)."""
    logger = logging.getLogger("switchboard")
    logger.setLevel(level.upper())
    if any(getattr(h, "_switchboard", False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler._switchboard = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.propagate = False


def log_request(ctx: RequestContext) -> None:
    access_log.info(
        "request",
        extra={
            "fields": {
                "request_id": ctx.request_id,
                "model": ctx.model_alias,
                "provider": ctx.provider,
                "status": ctx.status_code,
                "latency_ms": round(ctx.elapsed_ms(), 1),
                "cache": ctx.cache,
                "attempts": ctx.attempts,
                "tokens": ctx.usage.total_tokens if ctx.usage else 0,
                "cost_usd": ctx.cost_usd,
                "error": ctx.error,
            }
        },
    )
