"""Review pipeline tunables. All knobs in one frozen dataclass.

Defaults reflect what we've validated in production runs — change only
when you know why. CLI args (when `wordforge review` lands) will
override; until then this is imported directly.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewConfig:
    HAIKU_MODEL: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    OPUS_MODEL: str = "us.anthropic.claude-opus-4-7"
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
