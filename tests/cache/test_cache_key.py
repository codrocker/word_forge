"""canonical_cache_key from v9 spec §6: sha256 of canonical JSON of
[kind, model, request_params, rendered_prompt, input_payload].

Invariants: same inputs -> same key; JSON key order doesn't matter;
temperature changes the key; parser_version must NEVER be a kwarg."""

from __future__ import annotations

import pytest

from wordforge.cache import canonical_cache_key


def test_key_is_deterministic():
    k1 = canonical_cache_key(
        kind="llm:anthropic:claude-opus-4",
        model="claude-opus-4",
        request_params={"temperature": 0, "max_tokens": 2048},
        rendered_prompt="hello",
        input_payload={"word": "apple"},
    )
    k2 = canonical_cache_key(
        kind="llm:anthropic:claude-opus-4",
        model="claude-opus-4",
        request_params={"temperature": 0, "max_tokens": 2048},
        rendered_prompt="hello",
        input_payload={"word": "apple"},
    )
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_key_ignores_json_ordering_of_request_params():
    k1 = canonical_cache_key(
        kind="llm:anthropic:claude-opus-4",
        model="claude-opus-4",
        request_params={"temperature": 0, "max_tokens": 2048},
        rendered_prompt="hello",
        input_payload={"word": "apple"},
    )
    k2 = canonical_cache_key(
        kind="llm:anthropic:claude-opus-4",
        model="claude-opus-4",
        request_params={"max_tokens": 2048, "temperature": 0},
        rendered_prompt="hello",
        input_payload={"word": "apple"},
    )
    assert k1 == k2


def test_key_ignores_json_ordering_of_input_payload():
    k1 = canonical_cache_key(
        kind="dict:youdao",
        model="",
        request_params={},
        rendered_prompt="",
        input_payload={"word": "apple", "region": "us"},
    )
    k2 = canonical_cache_key(
        kind="dict:youdao",
        model="",
        request_params={},
        rendered_prompt="",
        input_payload={"region": "us", "word": "apple"},
    )
    assert k1 == k2


def test_different_temperature_yields_different_key():
    base = dict(
        kind="llm:anthropic:claude-opus-4",
        model="claude-opus-4",
        rendered_prompt="hello",
        input_payload={"word": "apple"},
    )
    k0 = canonical_cache_key(request_params={"temperature": 0}, **base)
    k1 = canonical_cache_key(request_params={"temperature": 0.5}, **base)
    assert k0 != k1


def test_different_prompt_yields_different_key():
    base = dict(
        kind="llm:anthropic:claude-opus-4",
        model="claude-opus-4",
        request_params={"temperature": 0},
        input_payload={"word": "apple"},
    )
    a = canonical_cache_key(rendered_prompt="hello", **base)
    b = canonical_cache_key(rendered_prompt="hello ", **base)  # trailing space
    assert a != b


def test_cache_key_rejects_parser_version_kwarg():
    """v9 spec §6 Round 3 D1 battle: parser_version must NOT influence cache_key.

    Behavioral guard: canonical_cache_key is kw-only with a fixed signature,
    so passing parser_version raises TypeError. If someone ever adds
    parser_version as a parameter, this test will fail because no TypeError
    is raised."""
    with pytest.raises(TypeError):
        canonical_cache_key(
            kind="llm:anthropic:claude-opus-4",
            model="m",
            request_params={},
            rendered_prompt="p",
            input_payload={},
            parser_version="1.0",  # type: ignore[call-arg]
        )
