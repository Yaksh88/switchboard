from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from switchboard.api.app import create_app
from switchboard.config import GatewayConfig, Settings


def make_config(**overrides: object) -> GatewayConfig:
    raw: dict[str, object] = {
        "providers": {"mock": {"type": "mock"}},
        "models": {"mock": {"targets": [{"provider": "mock", "model": "mock-1"}]}},
    }
    raw.update(overrides)
    return GatewayConfig.model_validate(raw)


@pytest.fixture
def gateway_config() -> GatewayConfig:
    return make_config()


@pytest.fixture
def client(gateway_config: GatewayConfig) -> Iterator[TestClient]:
    app = create_app(Settings(), gateway_config)
    with TestClient(app) as c:
        yield c
