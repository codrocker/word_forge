"""LLMClient: unified complete() that:
- hits external_call_cache on second identical call (no HTTP)
- honors bypass_cache=True to force re-call
- stores raw provider response
- treats 'anthropic' / 'openai' / 'gemini' as three providers with three SDKs

We stub the provider SDKs with a fake completer callable instead of mocking
httpx, because each provider SDK has its own HTTP stack under the hood.
"""

from __future__ import annotations

import pytest

from wordforge.cache import CacheStore
from wordforge.llm.client import LLMClient, LLMCompletion


class FakeCompleter:
    """Drop-in replacement for the provider SDK callable."""

    def __init__(self, response: dict, cost: float) -> None:
        self.response = response
        self.cost = cost
        self.calls = 0

    def __call__(self, **kwargs) -> LLMCompletion:
        self.calls += 1
        return LLMCompletion(response=self.response, cost_usd=self.cost)


def test_miss_then_hit_does_not_call_provider_twice(at_head):
    store = CacheStore(at_head)
    fake = FakeCompleter(response={"text": "hi"}, cost=0.003)
    client = LLMClient(store=store, completers={"anthropic": fake})

    r1 = client.complete(
        provider="anthropic",
        model="claude-opus-4",
        rendered_prompt="say hi",
        request_params={"temperature": 0, "max_tokens": 16},
        input_payload={"tag": "greeting"},
    )
    r2 = client.complete(
        provider="anthropic",
        model="claude-opus-4",
        rendered_prompt="say hi",
        request_params={"temperature": 0, "max_tokens": 16},
        input_payload={"tag": "greeting"},
    )
    assert r1.response == r2.response == {"text": "hi"}
    assert fake.calls == 1  # second call hit cache


def test_bypass_cache_forces_new_call(at_head):
    store = CacheStore(at_head)
    fake = FakeCompleter(response={"text": "hi"}, cost=0.003)
    client = LLMClient(store=store, completers={"anthropic": fake})

    for _ in range(3):
        client.complete(
            provider="anthropic",
            model="claude-opus-4",
            rendered_prompt="same",
            request_params={"temperature": 0},
            input_payload={"x": 1},
            bypass_cache=True,
        )
    assert fake.calls == 3


def test_different_temperature_misses_separately(at_head):
    store = CacheStore(at_head)
    fake = FakeCompleter(response={"text": "hi"}, cost=0.003)
    client = LLMClient(store=store, completers={"anthropic": fake})

    client.complete(
        provider="anthropic",
        model="claude-opus-4",
        rendered_prompt="p",
        request_params={"temperature": 0},
        input_payload={},
    )
    client.complete(
        provider="anthropic",
        model="claude-opus-4",
        rendered_prompt="p",
        request_params={"temperature": 0.5},
        input_payload={},
    )
    assert fake.calls == 2  # different temperature = different cache_key


def test_unknown_provider_raises(at_head):
    store = CacheStore(at_head)
    client = LLMClient(store=store, completers={})
    with pytest.raises(ValueError, match="unknown LLM provider"):
        client.complete(
            provider="magic",
            model="m",
            rendered_prompt="p",
            request_params={},
            input_payload={},
        )


def test_request_params_reserved_keys_rejected(at_head):
    """`request_params` must not shadow `model` or `prompt`,
    which LLMClient.complete() passes explicitly to the completer."""
    store = CacheStore(at_head)
    fake = FakeCompleter(response={"text": "hi"}, cost=0.003)
    client = LLMClient(store=store, completers={"anthropic": fake})

    for reserved in ("model", "prompt"):
        with pytest.raises(ValueError, match="reserved keys"):
            client.complete(
                provider="anthropic",
                model="claude-opus-4",
                rendered_prompt="p",
                request_params={reserved: "oops", "temperature": 0},
                input_payload={},
            )
    assert fake.calls == 0  # guard fires before provider invocation
