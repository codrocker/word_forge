"""Alibaba Qwen completer via DashScope OpenAI-compatible endpoint.

DashScope exposes an OpenAI-compatible /v1/chat/completions endpoint, so
we reuse the openai SDK. Key must be in DASHSCOPE_API_KEY env. Region
selection via WORDFORGE_DASHSCOPE_REGION: "us" (default, us-east) or
"intl" (Singapore).

Qwen is particularly strong on Chinese-first tasks. Considered for
mnemonic generation as an alternative to gemini-2.5-flash when the
pun style needs more idiomatic Chinese.

Pricing lookup falls through wordforge.llm.pricing (qwen-max / qwen3-max
entries there).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from wordforge.llm.client import LLMCompletion
from wordforge.llm.pricing import compute_cost

_log = logging.getLogger(__name__)


_REGION_URLS = {
    "us": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    "intl": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
}


def make_qwen_completer():
    """Create a completer backed by DashScope's OpenAI-compatible endpoint."""
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "openai not installed; `pip install openai` or "
            "`pip install wordforge[openai]`"
        ) from e

    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY env var not set")

    # Default to "intl" (Singapore) because the Key released in llm/README.md
    # is an intl key; us-east returns 401 for it. Override with
    # WORDFORGE_DASHSCOPE_REGION=us when using a US-issued key.
    region = os.environ.get("WORDFORGE_DASHSCOPE_REGION", "intl")
    base_url = _REGION_URLS.get(region)
    if base_url is None:
        raise RuntimeError(
            f"unknown WORDFORGE_DASHSCOPE_REGION={region!r}; "
            f"valid: {list(_REGION_URLS)}"
        )

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=float(os.environ.get("WORDFORGE_QWEN_TIMEOUT", "60")),
        max_retries=int(os.environ.get("WORDFORGE_QWEN_RETRIES", "2")),
    )

    def _completer(*, model: str, prompt: str, **params: Any) -> LLMCompletion:
        max_tokens = int(params.get("max_tokens", 2048))
        temperature = float(params.get("temperature", 0))

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = resp.choices[0] if resp.choices else None
        text = (choice.message.content or "") if choice else ""

        if not text:
            finish = getattr(choice, "finish_reason", "unknown") if choice else "no_choice"
            if finish == "content_filter":
                _log.warning("Qwen refused prompt (finish_reason=%s, model=%s)",
                             finish, model)
                return LLMCompletion(
                    response={"text": "", "in_tok": 0, "out_tok": 0},
                    cost_usd=0.0,
                )
            raise RuntimeError(
                f"Qwen returned empty content (finish_reason={finish}, model={model})"
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
    """Return {'qwen': completer} if DASHSCOPE_API_KEY is set, else {}."""
    if not os.environ.get("DASHSCOPE_API_KEY"):
        return {}
    try:
        return {"qwen": make_qwen_completer()}
    except RuntimeError:
        return {}
