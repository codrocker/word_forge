"""Single source of truth for LLM pricing (USD per 1M tokens).

Every completer reads from here instead of maintaining its own dict —
makes provider price changes a single-point edit. Unknown models fall
back to a safe mid-range default (sonnet-4 pricing) and log a warning.

Prices as of 2026-05-01. Update `CHANGELOG` list below when rates shift.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


# (input_rate, output_rate) per 1M tokens, USD.
_PRICES: dict[str, tuple[float, float]] = {
    # --- Anthropic via Bedrock or direct ---
    "claude-opus-4-7":               (15.0, 75.0),
    "claude-opus-4-6":               (15.0, 75.0),
    "claude-opus-4-5":               (15.0, 75.0),
    "claude-opus-4-1":               (15.0, 75.0),
    "claude-sonnet-4-6":             (3.0, 15.0),
    "claude-sonnet-4-5":             (3.0, 15.0),
    "claude-sonnet-4-2025":          (3.0, 15.0),
    "claude-haiku-4-5":              (1.0, 5.0),
    # --- Google Gemini (Vertex AI list prices) ---
    # Gemini 3.x preview (2026): tiered at 200k input tokens. We store the
    # ≤200k tier which covers every wordforge prompt (≤4k in). If you start
    # feeding long context you need a tier-aware lookup.
    # Thinking tokens bill as output — no separate rate.
    "gemini-3.1-pro-preview":        (2.0, 12.0),
    "gemini-3-pro-preview":          (2.0, 12.0),
    "gemini-3-flash-preview":        (0.50, 3.0),
    "gemini-3.1-flash-lite-preview": (0.25, 1.50),
    "gemini-2.5-pro":                (1.25, 5.0),
    "gemini-2.5-flash":              (0.075, 0.30),
    "gemini-2.5-flash-lite":         (0.05, 0.20),
    # --- Alibaba Qwen (DashScope US) ---
    "qwen-max":                      (2.0, 6.0),
    "qwen3-max":                     (2.0, 6.0),
    "qwen3.5-flash":                 (0.3, 1.2),
    # --- OpenAI direct + Azure OpenAI (same list prices) ---
    "gpt-5-pro":                     (15.0, 120.0),  # reasoning premium
    "gpt-5.1-codex":                 (5.0, 15.0),
    "gpt-5.1":                       (5.0, 15.0),
    "gpt-5-mini":                    (0.5, 2.0),
    "gpt-5-nano":                    (0.10, 0.40),
    "gpt-5":                         (5.0, 15.0),
    "gpt-4o-mini":                   (0.15, 0.60),
    "gpt-4o":                        (2.5, 10.0),
    "gpt-4.1":                       (3.0, 12.0),
    "o3-mini":                       (1.10, 4.40),
    # --- DeepSeek via Bedrock ---
    "deepseek.v3.2":                 (0.5, 2.0),
    "deepseek.r1":                   (1.0, 4.0),
    # --- DeepSeek direct API (2026-08 provider migration default) ---
    # V4-era list prices (2026-08-17, cross-checked against the pricing
    # table maintained in ~/.loongport): flash is the batch-pipeline tier,
    # pro the quality tier. chat/reasoner kept for V3.2-era naming.
    "deepseek-v4-flash":             (0.14, 0.28),
    "deepseek-v4-pro":               (0.435, 0.87),
    "deepseek-chat":                 (0.14, 0.28),
    "deepseek-reasoner":             (0.14, 0.28),
}


def price_per_million(model: str) -> tuple[float, float]:
    """Return (input_price, output_price) per 1M tokens.

    Matching is substring-based against the model string; the first key in
    `_PRICES` that appears in `model` wins. Example:
      "us.anthropic.claude-opus-4-7" → matches "claude-opus-4-7" → (15, 75)

    Unknown model falls back to sonnet-4 pricing (3/15) with a warning.
    Callers tracking cost shouldn't silently under-count.
    """
    for key, price in _PRICES.items():
        if key in model:
            return price
    _log.warning(
        "Unknown model %r for pricing lookup; defaulting to sonnet-4 ($3/$15). "
        "Add an entry to wordforge.llm.pricing._PRICES.",
        model,
    )
    return (3.0, 15.0)


def compute_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    """Convenience: dollars billed for this call."""
    in_rate, out_rate = price_per_million(model)
    return (in_tokens / 1_000_000 * in_rate) + (out_tokens / 1_000_000 * out_rate)
