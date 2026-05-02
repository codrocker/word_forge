"""OpenAI direct completer (chat completions API).

Activated when OPENAI_API_KEY env is set. Supports the full gpt-5 / gpt-5.1
series + gpt-5-pro, plus legacy gpt-4.1 / gpt-4o / o3-mini via the same
key. Model name is passed through verbatim (no "us." prefix fuss).

gpt-5 family uses max_completion_tokens instead of max_tokens in some SDK
versions; openai==2.x handles both transparently via `max_tokens` param.

Timeouts mirror bedrock_completer.py: 60s default read timeout via the
SDK's per-request `timeout` kwarg. Proxy-aware via OPENAI_BASE_URL if
you need to go through a proxy pool.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from wordforge.llm.client import LLMCompletion
from wordforge.llm.pricing import compute_cost

_log = logging.getLogger(__name__)


def make_openai_completer():
    """Create a completer backed by the openai SDK (Chat Completions API).

    Lazy import so CI without the SDK isn't forced to install it.
    """
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "openai not installed; `pip install openai` or "
            "`pip install wordforge[openai]`"
        ) from e

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY env var not set")

    client = OpenAI(
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        timeout=float(os.environ.get("WORDFORGE_OPENAI_TIMEOUT", "60")),
        max_retries=int(os.environ.get("WORDFORGE_OPENAI_RETRIES", "2")),
    )

    def _completer(*, model: str, prompt: str, **params: Any) -> LLMCompletion:
        max_tokens = int(params.get("max_tokens", 2048))
        temperature = float(params.get("temperature", 0))

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        # gpt-5* and o3* use max_completion_tokens and reject `temperature`
        # (they run at a fixed reasoning temperature). gpt-4* still expects
        # max_tokens + temperature. Branch on model prefix.
        if model.startswith(("gpt-5", "o3", "o1")):
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature

        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0] if resp.choices else None
        text = (choice.message.content or "") if choice else ""

        if not text:
            finish = getattr(choice, "finish_reason", "unknown") if choice else "no_choice"
            # OpenAI's content_filter parallels Bedrock's content_filtered:
            # soft-degrade to empty text + zero cost so a single policy
            # refusal doesn't kill the batch worker.
            if finish == "content_filter":
                _log.warning("OpenAI refused prompt (finish_reason=%s, model=%s)",
                             finish, model)
                return LLMCompletion(
                    response={"text": "", "in_tok": 0, "out_tok": 0},
                    cost_usd=0.0,
                )
            raise RuntimeError(
                f"OpenAI returned empty content (finish_reason={finish}, model={model})"
            )

        usage = resp.usage
        in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        cost = compute_cost(model, in_tok, out_tok)
        return LLMCompletion(
            response={"text": text, "in_tok": in_tok, "out_tok": out_tok},
            cost_usd=cost,
        )

    return _completer


def register_if_env_key() -> dict[str, Any]:
    """Return {'openai': completer} if OPENAI_API_KEY is set, else {}."""
    if not os.environ.get("OPENAI_API_KEY"):
        return {}
    try:
        return {"openai": make_openai_completer()}
    except RuntimeError:
        return {}
