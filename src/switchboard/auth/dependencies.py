from __future__ import annotations

import secrets

from fastapi import Request

from switchboard.auth.keys import KeyStore, Principal
from switchboard.errors import AdminUnavailableError, AuthenticationError


def _bearer_token(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthenticationError("Missing bearer token in the Authorization header")
    return token.strip()


async def require_principal(request: Request) -> Principal | None:
    """Authenticate the caller; returns ``None`` when auth is disabled (local dev)."""
    if not request.app.state.settings.auth_enabled:
        return None
    key_store: KeyStore = request.app.state.key_store
    principal = await key_store.authenticate(_bearer_token(request))
    if principal is None:
        raise AuthenticationError("Invalid or revoked API key")
    return principal


async def require_admin(request: Request) -> None:
    expected = request.app.state.settings.admin_token
    if not expected or request.app.state.key_store is None:
        raise AdminUnavailableError(
            "Admin API is disabled: set SWITCHBOARD_ADMIN_TOKEN and SWITCHBOARD_DATABASE_URL"
        )
    if not secrets.compare_digest(_bearer_token(request), expected):
        raise AuthenticationError("Invalid admin token")
