from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx

from switchboard.config import ProviderConfig
from switchboard.errors import ProviderError, ProviderTimeoutError
from switchboard.schemas import ChatCompletionChunk, ChatCompletionRequest, ChatCompletionResponse


class Provider(ABC):
    """Adapter between the OpenAI wire format and one upstream provider."""

    def __init__(self, name: str, config: ProviderConfig) -> None:
        self.name = name
        self.config = config

    @abstractmethod
    async def complete(self, req: ChatCompletionRequest, model: str) -> ChatCompletionResponse:
        """Run a non-streaming completion against upstream ``model``."""

    @abstractmethod
    def stream(self, req: ChatCompletionRequest, model: str) -> AsyncIterator[ChatCompletionChunk]:
        """Stream a completion. The final chunk carries ``usage`` when the upstream reports it."""

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass


class HttpProvider(Provider):
    """Base for providers reached over HTTP; owns a pooled client and error mapping."""

    default_base_url: str

    def __init__(self, name: str, config: ProviderConfig) -> None:
        super().__init__(name, config)
        self.client = httpx.AsyncClient(
            base_url=config.base_url or self.default_base_url,
            timeout=httpx.Timeout(config.timeout_s, connect=10.0),
            headers=self.auth_headers(),
        )

    @abstractmethod
    def auth_headers(self) -> dict[str, str]: ...

    async def aclose(self) -> None:
        await self.client.aclose()

    def raise_for_status(self, response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            body = response.json()
            detail = body.get("error", {}).get("message") or response.text
        except ValueError:
            detail = response.text
        raise ProviderError(self.name, detail[:500], status_code=response.status_code)

    def map_transport_error(self, exc: httpx.HTTPError) -> ProviderError:
        if isinstance(exc, httpx.TimeoutException):
            return ProviderTimeoutError(self.name)
        return ProviderError(self.name, f"transport error: {exc}", status_code=502, retryable=True)
