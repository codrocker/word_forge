"""Pure field-mapping functions: PG domain row -> MySQL word_forge row.

Spec: docs/superpowers/specs/2026-05-02-dual-write-mysql-design.md §5
"""

from __future__ import annotations

import json

# ruff: noqa: E501


def _id_list_to_json(ids: list[int], key: str = "id") -> str | None:
    """Convert [123, 456] -> '[{"id":123},{"id":456}]' per wiki convention.
    Empty list -> None (stored as NULL in MySQL, not empty '[]' string)."""
    if not ids:
        return None
    return json.dumps([{key: i} for i in ids], separators=(",", ":"))


def row_to_mysql_word(
    pg: dict,
    *,
    meaning_ids: list[int],
    mnemonic_ids: list[int],
    phrase_ids: list[int],
) -> dict:
    """Map one domain.words row + children id lists to MySQL word_forge.word row."""
    return {
        "word_id": pg["word_id"],
        "type": pg["type"],
        "form": pg["form"],
        "phonetic_us": pg.get("phonetic_us") or "",
        "audio_us": pg.get("audio_us"),
        "phonetic_uk": pg.get("phonetic_uk") or "",
        "audio_uk": pg.get("audio_uk"),
        "meanings": _id_list_to_json(meaning_ids),
        "mnemonics": _id_list_to_json(mnemonic_ids),
        "plural": None,
        "phrases": _id_list_to_json(phrase_ids),
        "structure": None,
        "third_person": None,
        "present_participle": None,
        "past_tense": None,
        "past_participle": None,
        "base": None,
        "comparative": None,
        "superlative": None,
        "derivatives": None,
        "morpheme_derivatives": None,
        "family": None,
        "source": pg.get("source"),
        "status": 1,
    }
