"""Review pipeline tunables. All knobs in one frozen dataclass.

Defaults reflect what we've validated in production runs — change only
when you know why. CLI args (when `wordforge review` lands) will
override; until then this is imported directly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewConfig:
    # LLM provider since the 2026-08 migration: all Bedrock/Gemini/OpenAI
    # accounts died; the openai completer points at any OpenAI-compatible
    # endpoint via OPENAI_BASE_URL (DeepSeek / Kimi / GLM / SiliconFlow).
    # Swap models here when the provider changes — config-only, no code.
    PROVIDER: str = "openai"
    # Checker tier (was claude-haiku-4-5 via bedrock).
    HAIKU_MODEL: str = "deepseek-chat"
    # Fixer tier (was claude-opus-4-7 via bedrock). If the 50-word
    # validation shows weak fixes, try deepseek-reasoner or kimi-k2 here.
    OPUS_MODEL: str = "deepseek-chat"
    HAIKU_MAX_TOKENS: int = 400
    OPUS_MAX_TOKENS: int = 2500
    # Input clipping — safety net for pathological polysemous words
    # (see rec["blob_truncated"] in jsonl if this fires).
    BLOB_CHAR_LIMIT: int = 5500
    ISSUES_CHAR_LIMIT: int = 3000
    EN_PARAPHRASE_CHAR_LIMIT: int = 180
    OPUS_PARSE_ERR_CHAR_LIMIT: int = 400
    # Control-plane timing
    STALL_SECS: float = 180.0
    HEARTBEAT_SECS: float = 30.0
    PROGRESS_EVERY: int = 50
    CALL_CONCURRENCY_MULTIPLIER: int = 3


CFG = ReviewConfig()
