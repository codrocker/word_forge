"""Pure functions that project flat DB rows → word-v1 nested dicts.

Spec: docs/superpowers/specs/2026-05-02-sailing-sqlite-packager-design.md §3-§6

Kept as pure functions (no DB, no IO) so the full mapping rules are unit-testable
without Postgres. The CLI layer feeds pre-fetched rows in.
"""

from __future__ import annotations

import re

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
