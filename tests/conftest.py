from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from switchboard.api.app import create_app
from switchboard.config import GatewayConfig, Settings

NO_DB = {"database_url": None, "auth_enabled": False}


def make_config(**overrides: Any) -> GatewayConfig:
    raw: dict[str, Any] = {
        "providers": {"mock": {"type": "mock"}},
        "models": {"mock": {"targets": [{"provider": "mock", "model": "mock-1"}]}},
        # Unit tests must not need Redis; integration tests turn the cache back on.
        "cache": {"enabled": False},
    }
    raw.update(overrides)
    return GatewayConfig.model_validate(raw)


@pytest.fixture
def gateway_config() -> GatewayConfig:
    return make_config()


@pytest.fixture
def client(gateway_config: GatewayConfig) -> Iterator[TestClient]:
    app = create_app(Settings(**NO_DB), gateway_config)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_client() -> Iterator[Callable[..., TestClient]]:
    """Factory for clients with a custom config: ``make_client(settings, cache={...})``."""
    opened: list[TestClient] = []

    def factory(settings: Settings | None = None, **overrides: Any) -> TestClient:
        app = create_app(settings or Settings(**NO_DB), make_config(**overrides))
        test_client = TestClient(app)
        test_client.__enter__()
        opened.append(test_client)
        return test_client

    yield factory
    for opened_client in opened:
        opened_client.__exit__(None, None, None)
