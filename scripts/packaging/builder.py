"""Pure functions that project flat DB rows → word-v1 nested dicts.

Kept as pure functions (no DB, no IO) so the full mapping rules are unit-testable
without Postgres. The CLI layer feeds pre-fetched rows in.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from scripts.packaging.pos_map import pos_display

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


WordRow = dict[str, Any]
MeaningRow = dict[str, Any]
SentenceRow = dict[str, Any]
MnemonicRow = dict[str, Any]


def _phonetic_block(form: str | None, audio: str | None) -> dict[str, str]:
    return {"form": form or "", "audio": audio or ""}


def build_word_payload(
    word: WordRow,
    *,
    meanings: list[MeaningRow],
    sentences_by_mid: dict[int, list[SentenceRow]],
    mnemonics: list[MnemonicRow],
) -> dict[str, Any]:
    """Compose one word-v1 JSON object. Pure function — no DB, no IO."""
    ph_us = _phonetic_block(word.get("phonetic_us"), word.get("audio_us"))
    ph_uk = _phonetic_block(word.get("phonetic_uk"), word.get("audio_uk"))
    return {
        "id": word["word_id"],
        "type": word["type"],
        "form": word["form"],
        "phonetic_us": ph_us,
        "phonetic_uk": ph_uk,
        "meanings": [
            _build_meaning(m, sentences_by_mid.get(m["meaning_id"], []), ph_us, ph_uk)
            for m in meanings
        ],
        "mnemonics": [_build_mnemonic(mn) for mn in mnemonics],
    }


def _build_meaning(
    m: MeaningRow,
    sentences: list[SentenceRow],
    ph_us: dict[str, str],
    ph_uk: dict[str, str],
) -> dict[str, Any]:
    pos_en, pos_cn = pos_display(m.get("pos"))
    return {
        "id": m["meaning_id"],
        "user_group": 0,
        "pos_en": pos_en,
        "pos_cn": pos_cn,
        "phonetic_us": ph_us,
        "phonetic_uk": ph_uk,
        "pos_meanings": split_pos_meanings(m.get("cn_paraphrase")),
        "sentences": [
            {
                "id": s["sentence_id"],
                "user_group": 0,
                "form": s["form"],
                "meaning": s["translation"],
                "audio": "",
                "is_collected": 0,
            }
            for s in sentences
        ],
    }


def _build_mnemonic(mn: MnemonicRow) -> dict[str, Any]:
    # TODO(spec §13 Q1): creator shape to be confirmed by frontend
    return {
        "id": mn["mnemonic_id"],
        "type": mn["type"],
        "user_group": 0,
        "creator": {},
        "is_pinned": 0,
        "content": extract_mnemonic_text(mn.get("content")),
    }
