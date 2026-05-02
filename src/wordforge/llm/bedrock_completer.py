"""AWS Bedrock provider completer — uses boto3 bedrock-runtime converse API.

Active when AWS_BEARER_TOKEN_BEDROCK or AWS_ACCESS_KEY_ID is set in env.
Model IDs follow the bedrock format: `us.anthropic.claude-sonnet-4-20250514-v1:0`.
"""

from __future__ import annotations

import os
from typing import Any

from wordforge.llm.client import LLMCompletion
from wordforge.llm.pricing import compute_cost


def make_bedrock_completer():
    """Create a completer backed by boto3 bedrock-runtime converse API.

    Lazy import so CI without boto3 isn't forced to install it.

    Timeouts (CLAUDE.md hard rule — Bedrock-from-China reality):
      - connect_timeout: 10s (env: WORDFORGE_BEDROCK_CONNECT_TIMEOUT)
      - read_timeout:   60s (env: WORDFORGE_BEDROCK_READ_TIMEOUT)
      - retries: 2 attempts
    Without these, a half-dead SOCKS5 proxy leaves the request hanging
    forever — ESTABLISHED TCP, zero bytes, no timeout fires.
    """
    try:
        import boto3  # type: ignore[import-not-found]
        from botocore.config import Config  # type: ignore[import-not-found]
    except ImportError as e:
        raise RuntimeError(
            "boto3 not installed; `pip install boto3` or `pip install wordforge[llm]`"
        ) from e

    region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
    cfg = Config(
        connect_timeout=int(os.environ.get("WORDFORGE_BEDROCK_CONNECT_TIMEOUT", "10")),
        read_timeout=int(os.environ.get("WORDFORGE_BEDROCK_READ_TIMEOUT", "60")),
        retries={"max_attempts": 2, "mode": "standard"},
    )
    client = boto3.client("bedrock-runtime", region_name=region, config=cfg)

    def _completer(*, model: str, prompt: str, **params: Any) -> LLMCompletion:
        max_tokens = int(params.get("max_tokens", 2048))
        inference_cfg: dict[str, Any] = {"maxTokens": max_tokens}
        # Opus 4.7 deprecated the temperature parameter and rejects requests
        # that include it. Older models still accept (and default to) it.
        if "claude-opus-4-7" not in model:
            inference_cfg["temperature"] = float(params.get("temperature", 0))

        response = client.converse(
            modelId=model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig=inference_cfg,
        )
        stop_reason = response.get("stopReason", "unknown")
        content = response.get("output", {}).get("message", {}).get("content", [])
        if not content:
            # content_filtered = Bedrock Guardrails / safety refused this
            # specific prompt. NOT a system error — return empty text +
            # zero cost so callers treat it as "model declined to answer"
            # and keep processing the rest of the batch. Other empty-content
            # states (e.g. unknown stop_reason) still fail-loud.
            if stop_reason == "content_filtered":
                return LLMCompletion(
                    response={"text": "", "in_tok": 0, "out_tok": 0},
                    cost_usd=0.0,
                )
            raise RuntimeError(
                f"Bedrock returned empty content (stop_reason={stop_reason}, model={model})"
            )
        text = content[0].get("text", "")
        usage = response.get("usage", {})
        in_tok = int(usage.get("inputTokens", 0))
        out_tok = int(usage.get("outputTokens", 0))
        cost = compute_cost(model, in_tok, out_tok)
        return LLMCompletion(
            response={"text": text, "in_tok": in_tok, "out_tok": out_tok},
            cost_usd=cost,
        )

    return _completer


def register_if_env_key() -> dict[str, Any]:
    """Return a {provider: completer} dict; empty if no AWS creds or boto3 missing.

    Provider key is `bedrock` (not `anthropic`) so plans can target either.
    Gracefully returns {} when boto3 is not installed (e.g. dev/test envs
    without the [llm] extra) so the CLI doesn't crash on --help or non-LLM runs.
    """
    has_bearer = bool(os.environ.get("AWS_BEARER_TOKEN_BEDROCK"))
    has_static = bool(os.environ.get("AWS_ACCESS_KEY_ID"))
    if not (has_bearer or has_static):
        return {}
    try:
        return {"bedrock": make_bedrock_completer()}
    except RuntimeError:
        # boto3 not installed; fall through to anthropic or no-LLM mode.
        return {}
