from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from switchboard.auth.dependencies import require_admin
from switchboard.auth.keys import KeyStore
from switchboard.errors import NotFoundError

router = APIRouter(prefix="/admin/keys", tags=["admin"], dependencies=[Depends(require_admin)])


class CreateKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rpm_limit: int | None = Field(default=None, gt=0)
    tpm_limit: int | None = Field(default=None, gt=0)
    monthly_budget_usd: float | None = Field(default=None, gt=0)
    allowed_models: list[str] | None = None


class KeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    key_prefix: str
    rpm_limit: int | None
    tpm_limit: int | None
    monthly_budget_usd: float | None
    allowed_models: list[str] | None
    created_at: datetime
    revoked_at: datetime | None


class CreatedKey(KeyOut):
    key: str


def _store(request: Request) -> KeyStore:
    store: KeyStore = request.app.state.key_store
    return store


@router.post("", status_code=201)
async def create_key(body: CreateKeyRequest, request: Request) -> CreatedKey:
    key, raw = await _store(request).create(**body.model_dump())
    return CreatedKey(**KeyOut.model_validate(key).model_dump(), key=raw)


@router.get("")
async def list_keys(request: Request) -> list[KeyOut]:
    return [KeyOut.model_validate(k) for k in await _store(request).list()]


@router.delete("/{key_id}", status_code=204)
async def revoke_key(key_id: uuid.UUID, request: Request) -> Response:
    if not await _store(request).revoke(key_id):
        raise NotFoundError(f"No active key with id {key_id}")
    return Response(status_code=204)
