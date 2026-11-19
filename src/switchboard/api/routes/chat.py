from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from switchboard.context import RequestContext
from switchboard.errors import GatewayError
from switchboard.gateway import Gateway
from switchboard.schemas import ChatCompletionRequest

router = APIRouter(prefix="/v1", tags=["chat"])


def _gateway(request: Request) -> Gateway:
    gateway: Gateway = request.app.state.gateway
    return gateway


def _sse(data: str) -> str:
    return f"data: {data}\n\n"


async def _event_stream(
    gateway: Gateway, req: ChatCompletionRequest, ctx: RequestContext
) -> AsyncIterator[str]:
    try:
        async for chunk in gateway.stream(req, ctx):
            yield _sse(chunk.model_dump_json(exclude_none=True))
    except GatewayError as exc:
        # Headers are already sent, so the status code can't change; surface the error
        # in-band the same way OpenAI does.
        yield _sse(json.dumps({"error": {"message": exc.message, "type": exc.error_type}}))
    yield _sse("[DONE]")


@router.post("/chat/completions", response_model=None)
async def chat_completions(
    body: ChatCompletionRequest, request: Request
) -> JSONResponse | StreamingResponse:
    gateway = _gateway(request)
    ctx = RequestContext(model_alias=body.model, stream=body.stream)
    if body.stream:
        # The serving provider isn't known until a chunk arrives, after headers are sent,
        # so streams only carry the request id.
        return StreamingResponse(
            _event_stream(gateway, body, ctx),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "x-request-id": ctx.request_id,
            },
        )
    response = await gateway.complete(body, ctx)
    return JSONResponse(response.model_dump(), headers=ctx.headers())


@router.get("/models")
async def list_models(request: Request) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "owned_by": "switchboard"}
            for m in _gateway(request).models()
        ],
    }
