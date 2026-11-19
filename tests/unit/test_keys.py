from __future__ import annotations

from switchboard.auth.keys import generate_key, hash_key


def test_generated_keys_are_prefixed_and_unique() -> None:
    keys = {generate_key() for _ in range(100)}
    assert len(keys) == 100
    assert all(k.startswith("sb-") and len(k) >= 40 for k in keys)


def test_hash_is_stable_sha256_hex() -> None:
    digest = hash_key("sb-example")
    assert digest == hash_key("sb-example")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    assert digest != hash_key("sb-other")
