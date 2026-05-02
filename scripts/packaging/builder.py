"""Pure functions that project flat DB rows → word-v1 nested dicts.

Spec: docs/superpowers/specs/2026-05-02-sailing-sqlite-packager-design.md §3-§6

Kept as pure functions (no DB, no IO) so the full mapping rules are unit-testable
without Postgres. The CLI layer feeds pre-fetched rows in.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

_logger = logging.getLogger(__name__)

_SEMI_RE = re.compile(r"[；;]")


def split_pos_meanings(cn: str | None) -> list[str]:
    """Spec §6 Q2(b): split cn_paraphrase on full/half-width semicolon only.

    Commas (，/,) and ideographic comma 、 stay inside each segment. Strip
    whitespace per segment and drop empty segments.
    """
    if not cn or not cn.strip():
        return []
    parts = _SEMI_RE.split(cn)
    return [p.strip() for p in parts if p.strip()]


def extract_mnemonic_text(content: Any) -> str:
    """Spec §4: domain.mnemonics.content is JSONB {"kind","text"}; return text.

    Defensive: missing / non-str text → "" with warning. Accepts both dict
    (normal driver behavior) and raw JSON str (driver fallback).
    """
    if content is None:
        return ""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            _logger.warning("mnemonic.content is str but not JSON: %r", content[:80])
            return ""
    if not isinstance(content, dict):
        _logger.warning("mnemonic.content is %s, expected dict", type(content).__name__)
        return ""
    text = content.get("text")
    if not isinstance(text, str) or not text:
        _logger.warning(
            "mnemonic.content.text missing or non-str: keys=%r", list(content.keys())
        )
        return ""
    return text
