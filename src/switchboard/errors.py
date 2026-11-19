"""Gateway error hierarchy.

Every error carries an HTTP status and an OpenAI-style ``type`` so the API layer
can render it uniformly. ``retryable`` drives the router's fallback decisions.
"""

from __future__ import annotations


class GatewayError(Exception):
    status_code: int = 500
    error_type: str = "server_error"
    retryable: bool = False

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class InvalidRequestError(GatewayError):
    status_code = 400
    error_type = "invalid_request_error"


class AuthenticationError(GatewayError):
    status_code = 401
    error_type = "authentication_error"


class PermissionDeniedError(GatewayError):
    status_code = 403
    error_type = "permission_error"


class NotFoundError(GatewayError):
    status_code = 404
    error_type = "invalid_request_error"


class AdminUnavailableError(GatewayError):
    status_code = 503
    error_type = "server_error"


class RateLimitError(GatewayError):
    status_code = 429
    error_type = "rate_limit_error"

    def __init__(
        self,
        message: str,
        *,
        retry_after_s: float,
        code: str = "rate_limit_exceeded",
        remaining: int | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.retry_after_s = retry_after_s
        self.remaining = remaining


class ModelNotFoundError(GatewayError):
    status_code = 404
    error_type = "invalid_request_error"

    def __init__(self, model: str) -> None:
        super().__init__(
            f"Model '{model}' is not configured on this gateway", code="model_not_found"
        )


class ProviderError(GatewayError):
    """An upstream provider failed. ``status_code`` mirrors the upstream status."""

    status_code = 502
    error_type = "upstream_error"

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        if status_code is not None:
            self.status_code = status_code
        # 429 and 5xx are transient; 4xx (bad request, auth) are not worth retrying elsewhere
        # because the same request will fail the same way.
        self.retryable = (
            retryable
            if retryable is not None
            else self.status_code == 429 or self.status_code >= 500
        )


class ProviderTimeoutError(ProviderError):
    def __init__(self, provider: str) -> None:
        super().__init__(provider, "request timed out", status_code=504, retryable=True)
