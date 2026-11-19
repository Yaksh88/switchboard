from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from switchboard.config import GatewayConfig, load_gateway_config


def test_env_vars_are_expanded_with_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_KEY", "sk-123")
    monkeypatch.delenv("MISSING_URL", raising=False)
    path = tmp_path / "gw.yaml"
    path.write_text(
        """
providers:
  oai: { type: openai, api_key: "${TEST_KEY}", base_url: "${MISSING_URL:-http://local/v1}" }
models:
  m: { targets: [{ provider: oai, model: x }] }
"""
    )
    config = load_gateway_config(path)
    assert config.providers["oai"].api_key == "sk-123"
    assert config.providers["oai"].base_url == "http://local/v1"


def test_unknown_provider_reference_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown provider 'nope'"):
        GatewayConfig.model_validate(
            {"providers": {}, "models": {"m": {"targets": [{"provider": "nope", "model": "x"}]}}}
        )


def test_example_config_is_valid() -> None:
    load_gateway_config(Path(__file__).parents[2] / "config" / "switchboard.example.yaml")
