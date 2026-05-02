"""Azure OpenAI completer — two-endpoint support (EP1 legacy, EP2 gpt-5).

Endpoints:
  - EP1 (openai-hytech-ai): gpt-4o, gpt-4o-mini, gpt-4.1, embedding
  - EP2 (mark-me9petcf):    gpt-5, gpt-5-mini, gpt-5.1 (Responses API)

Model routing picks the endpoint based on model name prefix:
  gpt-5* → EP2     (env: AZURE_OPENAI_EP2_KEY / _ENDPOINT)
  gpt-4* → EP1     (env: AZURE_OPENAI_EP1_KEY / _ENDPOINT)

At least one endpoint's credentials must be set for the provider to
register. Missing endpoint for a requested model raises RuntimeError at
call time so the stage fails loud.

Note: Azure EP2 (gpt-5 family) requires `max_completion_tokens` rather
than `max_tokens`; the openai SDK >= 1.50 auto-routes, but we still
need to pass the right arg name ourselves when using Chat Completions
path (we do).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from wordforge.llm.client import LLMCompletion
from wordforge.llm.pricing import compute_cost

_log = logging.getLogger(__name__)


def make_azure_completer():
    """Create a completer with routing across two Azure endpoints."""
    try:
        from openai import AzureOpenAI  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "openai not installed; `pip install openai` or "
            "`pip install wordforge[openai]`"
        ) from e

    ep1_key = os.environ.get("AZURE_OPENAI_EP1_KEY")
    ep1_endpoint = os.environ.get("AZURE_OPENAI_EP1_ENDPOINT")
    ep2_key = os.environ.get("AZURE_OPENAI_EP2_KEY")
    ep2_endpoint = os.environ.get("AZURE_OPENAI_EP2_ENDPOINT")

    api_version_default = os.environ.get(
        "AZURE_OPENAI_API_VERSION", "2024-12-01-preview"
    )
    timeout = float(os.environ.get("WORDFORGE_AZURE_TIMEOUT", "60"))
    retries = int(os.environ.get("WORDFORGE_AZURE_RETRIES", "2"))

    ep1_client = None
    ep2_client = None
    if ep1_key and ep1_endpoint:
        ep1_client = AzureOpenAI(
            api_key=ep1_key,
            azure_endpoint=ep1_endpoint,
            api_version=api_version_default,
            timeout=timeout,
            max_retries=retries,
        )
    if ep2_key and ep2_endpoint:
        ep2_client = AzureOpenAI(
            api_key=ep2_key,
            azure_endpoint=ep2_endpoint,
            api_version=api_version_default,
            timeout=timeout,
            max_retries=retries,
        )
    if ep1_client is None and ep2_client is None:
        raise RuntimeError(
            "No Azure endpoint credentials: set at least one of "
            "AZURE_OPENAI_EP1_KEY+EP1_ENDPOINT or AZURE_OPENAI_EP2_KEY+EP2_ENDPOINT"
        )

    def _pick_client(model: str):
        if model.startswith(("gpt-5", "o1", "o3")):
            if ep2_client is None:
                raise RuntimeError(
                    f"model {model!r} requires EP2 (AZURE_OPENAI_EP2_KEY+_ENDPOINT) "
                    "but EP2 credentials are not set"
                )
            return ep2_client
        if ep1_client is None:
            raise RuntimeError(
                f"model {model!r} requires EP1 (AZURE_OPENAI_EP1_KEY+_ENDPOINT) "
                "but EP1 credentials are not set"
            )
        return ep1_client

    def _completer(*, model: str, prompt: str, **params: Any) -> LLMCompletion:
        max_tokens = int(params.get("max_tokens", 2048))
        temperature = float(params.get("temperature", 0))
        client = _pick_client(model)

        kwargs: dict[str, Any] = {
            "model": model,  # Azure deployment name must match this
            "messages": [{"role": "user", "content": prompt}],
        }
        if model.startswith(("gpt-5", "o1", "o3")):
            kwargs["max_completion_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens
            kwargs["temperature"] = temperature

        resp = client.chat.completions.create(**kwargs)
        choice = resp.choices[0] if resp.choices else None
        text = (choice.message.content or "") if choice else ""

        if not text:
            finish = getattr(choice, "finish_reason", "unknown") if choice else "no_choice"
            if finish == "content_filter":
                _log.warning("Azure OpenAI refused prompt (finish_reason=%s, model=%s)",
                             finish, model)
                return LLMCompletion(
                    response={"text": "", "in_tok": 0, "out_tok": 0},
                    cost_usd=0.0,
                )
            raise RuntimeError(
                f"Azure OpenAI returned empty content (finish_reason={finish}, model={model})"
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
    """Return {'azure': completer} if either EP1 or EP2 credentials exist."""
    has_ep1 = bool(
        os.environ.get("AZURE_OPENAI_EP1_KEY")
        and os.environ.get("AZURE_OPENAI_EP1_ENDPOINT")
    )
    has_ep2 = bool(
        os.environ.get("AZURE_OPENAI_EP2_KEY")
        and os.environ.get("AZURE_OPENAI_EP2_ENDPOINT")
    )
    if not (has_ep1 or has_ep2):
        return {}
    try:
        return {"azure": make_azure_completer()}
    except RuntimeError:
        return {}
