from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from switchboard import __version__
from switchboard.api.routes import chat, health
from switchboard.config import GatewayConfig, Settings, load_gateway_config
from switchboard.errors import GatewayError
from switchboard.gateway import Gateway
from switchboard.schemas import ErrorBody, ErrorResponse

log = logging.getLogger("switchboard")


def error_response(
    status: int, message: str, error_type: str, code: str | None = None
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(message=message, type=error_type, code=code))
    return JSONResponse(body.model_dump(), status_code=status)


def create_app(
    settings: Settings | None = None, gateway_config: GatewayConfig | None = None
) -> FastAPI:
    settings = settings or Settings()
    gateway_config = gateway_config or load_gateway_config(settings.config_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.gateway = Gateway(gateway_config)
        log.info("switchboard started with models: %s", ", ".join(app.state.gateway.models()))
        yield
        await app.state.gateway.aclose()

    app = FastAPI(title="Switchboard", version=__version__, lifespan=lifespan)

    @app.exception_handler(GatewayError)
    async def _gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        return error_response(exc.status_code, exc.message, exc.error_type, exc.code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"] if p != "body")
        return error_response(400, f"{loc}: {first['msg']}", "invalid_request_error")

    app.include_router(health.router)
    app.include_router(chat.router)
    return app
