"""LLMClient — unified interface over anthropic/openai/gemini SDKs.

Stage code calls `LLMClient.complete(...)` and the class:
1. Computes canonical cache_key via wordforge.cache.canonical_cache_key
2. Looks up pipeline.external_call_cache by (kind, cache_key)
3. On hit: returns the stored response (zero external cost)
4. On miss: invokes the provider completer, stores (key, raw response, cost),
   returns LLMCompletion

The class does NOT:
- build prompts (stage code does that)
- parse structured output (stage code does that)
- retry on transient errors (runner does that, P4)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from wordforge.cache import CacheStore, canonical_cache_key


@dataclass
class LLMCompletion:
    response: dict[str, Any]
    cost_usd: float


Completer = Callable[..., LLMCompletion]


@dataclass
class LLMClient:
    store: CacheStore
    completers: dict[str, Completer] = field(default_factory=dict)

    def complete(
        self,
        *,
        provider: str,
        model: str,
        rendered_prompt: str,
        request_params: dict[str, Any],
        input_payload: dict[str, Any],
        bypass_cache: bool = False,
    ) -> LLMCompletion:
        if provider not in self.completers:
            raise ValueError(f"unknown LLM provider: {provider!r}")

        # Fail-fast: `request_params` gets spread into the completer call as
        # `**request_params`. If it contains `model` or `prompt`, Python raises
        # a cryptic "multiple values for keyword argument" TypeError. Reject
        # up front so stage code (P5) sees a clear message instead of a
        # stacktrace during a real LLM call.
        reserved = {"model", "prompt"} & request_params.keys()
        if reserved:
            raise ValueError(f"request_params must not contain reserved keys: {sorted(reserved)}")

        # 3-segment `llm:{provider}:{model}` — explicit and future-proof for
        # cases where the same model name is served by multiple providers
        # (e.g. a model mirrored on both OpenAI and Azure OpenAI). Spec §4
        # DDL comment uses a 2-segment example like `'llm:claude-opus'`;
        # per Round 1 battle D1, the plan's 3-segment form is the canonical
        # choice and the spec comment should follow this plan, not the
        # other way around.
        kind = f"llm:{provider}:{model}"
        key = canonical_cache_key(
            kind=kind,
            model=model,
            request_params=request_params,
            rendered_prompt=rendered_prompt,
            input_payload=input_payload,
        )

        if not bypass_cache:
            row = self.store.get(kind, key)
            if row is not None:
                return LLMCompletion(response=row["response"], cost_usd=float(row["cost_usd"]))

        completion = self.completers[provider](
            model=model,
            prompt=rendered_prompt,
            **request_params,
        )
        self.store.put(
            kind=kind,
            cache_key=key,
            response=completion.response,
            cost_usd=completion.cost_usd,
        )
        return completion
