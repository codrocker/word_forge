"""Real anthropic provider completer. Lazy import so unit tests never need the SDK."""

from __future__ import annotations

import os
from typing import Any

from wordforge.llm.client import LLMCompletion


def make_anthropic_completer(*, api_key: str | None = None):
    """Create a completer backed by the anthropic SDK.

    Lazy import — only load the SDK when an env key is present and this
    factory is actually called. CI tests using stub completers never touch it.

    `api_key` serves named [providers.*] entries; omitted it falls back to
    ANTHROPIC_API_KEY (the pre-registry behavior).
    """
    try:
        import anthropic  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "anthropic SDK not installed; `pip install anthropic` to use provider"
        ) from e

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def _completer(*, model: str, prompt: str, **params: Any) -> LLMCompletion:
        resp = client.messages.create(
            model=model,
            max_tokens=params.get("max_tokens", 2048),
            temperature=params.get("temperature", 0),
            messages=[{"role": "user", "content": prompt}],
        )
        # claude response: resp.content is a list of TextBlock; concatenate.
        text = "".join(
            block.text for block in resp.content if getattr(block, "type", None) == "text"
        )
        # Rough cost proxy: opus-4 $15/M input, $75/M output (Anthropic pricing).
        in_tok = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
        cost = (in_tok / 1_000_000 * 15.0) + (out_tok / 1_000_000 * 75.0)
        return LLMCompletion(
            response={"text": text, "in_tok": in_tok, "out_tok": out_tok}, cost_usd=cost
        )

    return _completer


def register_if_env_key() -> dict[str, Any]:
    """Return a {provider: completer} dict; empty if env key absent."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {}
    return {"anthropic": make_anthropic_completer()}
