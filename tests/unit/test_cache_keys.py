from __future__ import annotations

from typing import Any

from switchboard.cache.keys import exact_key, is_cacheable, semantic_namespace
from switchboard.schemas import ChatCompletionRequest


def req(**overrides: Any) -> ChatCompletionRequest:
    body: dict[str, Any] = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
        ],
    }
    body.update(overrides)
    return ChatCompletionRequest.model_validate(body)


def test_exact_key_ignores_stream_flag_and_user_field() -> None:
    assert exact_key(req(stream=True, user="a")) == exact_key(req(stream=False, user="b"))


def test_exact_key_changes_with_messages_and_temperature() -> None:
    base = exact_key(req())
    assert exact_key(req(messages=[{"role": "user", "content": "other"}])) != base
    assert exact_key(req(temperature=0)) != base
    assert exact_key(req(model="other")) != base
    assert base.startswith("sb:cache:exact:")


def test_nonzero_temperature_bypasses_cache() -> None:
    assert is_cacheable(req(temperature=0.7)) is False
    assert is_cacheable(req()) is True
    assert is_cacheable(req(temperature=0)) is True


def test_semantic_namespace_differs_by_system_prompt() -> None:
    other = req(
        messages=[
            {"role": "system", "content": "be verbose"},
            {"role": "user", "content": "hello"},
        ]
    )
    assert semantic_namespace(req()) != semantic_namespace(other)


def test_semantic_namespace_ignores_the_last_message() -> None:
    other = req(
        messages=[
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "a different question"},
        ]
    )
    assert semantic_namespace(req()) == semantic_namespace(other)
