from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from math import ceil

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from switchboard import __version__
from switchboard.api.routes import admin_keys, chat, health
from switchboard.auth.keys import KeyStore
from switchboard.config import GatewayConfig, Settings, load_gateway_config
from switchboard.db.engine import create_engine, session_factory
from switchboard.errors import GatewayError, RateLimitError
from switchboard.gateway import Gateway
from switchboard.redis import create_redis
from switchboard.schemas import ErrorBody, ErrorResponse

log = logging.getLogger("switchboard")


def error_response(
    status: int,
    message: str,
    error_type: str,
    code: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(message=message, type=error_type, code=code))
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


def create_app(
    settings: Settings | None = None, gateway_config: GatewayConfig | None = None
) -> FastAPI:
    settings = settings or Settings()
    gateway_config = gateway_config or load_gateway_config(settings.config_path)
    if settings.auth_enabled and settings.database_url is None:
        raise ValueError(
            "auth is enabled but no database is configured; "
            "set SWITCHBOARD_DATABASE_URL or SWITCHBOARD_AUTH_ENABLED=false"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url) if settings.database_url else None
        app.state.key_store = KeyStore(session_factory(engine)) if engine else None
        redis = create_redis(settings.redis_url)
        app.state.gateway = Gateway(gateway_config, redis)
        log.info("switchboard started with models: %s", ", ".join(app.state.gateway.models()))
        yield
        await app.state.gateway.aclose()
        await redis.aclose()
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="Switchboard", version=__version__, lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(GatewayError)
    async def _gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitError):
            headers["retry-after"] = str(max(1, ceil(exc.retry_after_s)))
            if exc.remaining is not None:
                headers["x-ratelimit-remaining-requests"] = str(exc.remaining)
        return error_response(exc.status_code, exc.message, exc.error_type, exc.code, headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"] if p != "body")
        return error_response(400, f"{loc}: {first['msg']}", "invalid_request_error")

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(admin_keys.router)
    return app
