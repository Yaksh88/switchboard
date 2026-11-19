from __future__ import annotations

import hashlib
import secrets
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from switchboard.db.models import ApiKey

KEY_PREFIX = "sb-"
DISPLAY_PREFIX_LEN = 12


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, as the rest of the pipeline sees it."""

    key_id: uuid.UUID
    name: str
    rpm_limit: int | None
    tpm_limit: int | None
    monthly_budget_usd: float | None
    allowed_models: tuple[str, ...] | None

    @classmethod
    def from_key(cls, key: ApiKey) -> Principal:
        return cls(
            key_id=key.id,
            name=key.name,
            rpm_limit=key.rpm_limit,
            tpm_limit=key.tpm_limit,
            monthly_budget_usd=(
                float(key.monthly_budget_usd) if key.monthly_budget_usd is not None else None
            ),
            allowed_models=tuple(key.allowed_models) if key.allowed_models is not None else None,
        )


class KeyStore:
    """Issues, verifies and revokes API keys.

    Successful lookups are cached in-process for ``cache_ttl_s`` so the hot path doesn't
    hit Postgres on every request; revoking through this store evicts immediately.
    """

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], cache_ttl_s: float = 60.0
    ) -> None:
        self.sessions = sessions
        self.cache_ttl_s = cache_ttl_s
        self._cache: dict[str, tuple[float, Principal]] = {}

    async def authenticate(self, raw: str) -> Principal | None:
        digest = hash_key(raw)
        cached = self._cache.get(digest)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]

        async with self.sessions() as session:
            key = await session.scalar(
                select(ApiKey).where(ApiKey.key_hash == digest, ApiKey.revoked_at.is_(None))
            )
        if key is None:
            self._cache.pop(digest, None)
            return None
        principal = Principal.from_key(key)
        self._cache[digest] = (time.monotonic() + self.cache_ttl_s, principal)
        return principal

    async def create(
        self,
        name: str,
        *,
        rpm_limit: int | None = None,
        tpm_limit: int | None = None,
        monthly_budget_usd: float | None = None,
        allowed_models: list[str] | None = None,
    ) -> tuple[ApiKey, str]:
        """Create a key; the raw secret is returned here and never again."""
        raw = generate_key()
        key = ApiKey(
            name=name,
            key_prefix=raw[:DISPLAY_PREFIX_LEN],
            key_hash=hash_key(raw),
            rpm_limit=rpm_limit,
            tpm_limit=tpm_limit,
            monthly_budget_usd=Decimal(str(monthly_budget_usd))
            if monthly_budget_usd is not None
            else None,
            allowed_models=allowed_models,
        )
        async with self.sessions() as session:
            session.add(key)
            await session.commit()
        return key, raw

    async def list(self) -> list[ApiKey]:
        async with self.sessions() as session:
            result = await session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc()))
            return list(result)

    async def revoke(self, key_id: uuid.UUID) -> bool:
        async with self.sessions() as session:
            result = await session.execute(
                update(ApiKey)
                .where(ApiKey.id == key_id, ApiKey.revoked_at.is_(None))
                .values(revoked_at=func.now())
                .returning(ApiKey.key_hash)
            )
            digest = result.scalar_one_or_none()
            await session.commit()
        if digest is None:
            return False
        self._cache.pop(digest, None)
        return True
