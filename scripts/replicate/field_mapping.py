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


def row_to_mysql_meaning(pg: dict, *, sentence_ids: list[int]) -> dict:
    """Map one domain.meanings row + its sentence id list."""
    equivalents_raw = pg.get("equivalents")
    equivalents_json = (
        json.dumps(equivalents_raw, ensure_ascii=False, separators=(",", ":"))
        if equivalents_raw else None
    )
    synonyms_raw = pg.get("synonyms")
    synonyms_json = (
        json.dumps(synonyms_raw, ensure_ascii=False, separators=(",", ":"))
        if synonyms_raw else None
    )
    antonyms_raw = pg.get("antonyms")
    antonyms_json = (
        json.dumps(antonyms_raw, ensure_ascii=False, separators=(",", ":"))
        if antonyms_raw else None
    )
    return {
        "meaning_id": pg["meaning_id"],
        "word_id": pg["word_id"],
        "user_group": None,
        "pos": pg.get("pos"),
        "pos_sub": None,
        "equivalents": equivalents_json,
        "synonyms": synonyms_json,
        "antonyms": antonyms_json,
        "phonetic_us": pg.get("phonetic_us"),
        "audio_us": pg.get("audio_us"),
        "phonetic_uk": pg.get("phonetic_uk"),
        "audio_uk": pg.get("audio_uk"),
        "cn_paraphrase": pg.get("cn_paraphrase"),
        "en_paraphrase": pg.get("en_paraphrase"),
        "sentences": _id_list_to_json(sentence_ids, key="sentence_id"),
        "source": pg.get("source"),
    }


def row_to_mysql_sentence(pg: dict) -> dict:
    """Map one domain.sentences row."""
    highlight = pg.get("highlight")
    highlight_json = (
        json.dumps(highlight, separators=(",", ":")) if highlight else None
    )
    return {
        "sentence_id": pg["sentence_id"],
        "word_id": pg["word_id"],
        "meaning_id": pg["meaning_id"],
        "user_group": None,
        "form": pg.get("form"),
        "highlight": highlight_json,
        "translation": pg["translation"],
        "audio_us": None,
        "audio_uk": None,
        "source": pg.get("source"),
        "citation": pg.get("citation"),
        "citation_detail": pg.get("citation_detail"),
    }


def row_to_mysql_mnemonic(pg: dict) -> dict:
    """Map one domain.mnemonics row."""
    content = pg["content"]
    content_json = (
        json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        if isinstance(content, dict) else str(content)
    )
    return {
        "mnemonic_id": pg["mnemonic_id"],
        "word_id": pg["word_id"],
        "type": pg["type"],
        "user_group": 0,
        "content": content_json,
        "source": pg.get("source"),
        "creator_id": 0,
    }


def row_to_mysql_phrase(pg: dict) -> dict:
    """Map one domain.phrases row."""
    return {
        "phrase_id": pg["phrase_id"],
        "form": pg["form"],
        "meaning": pg["meaning"],
        "audio_us": pg.get("audio_us") or "",
        "audio_uk": pg.get("audio_uk") or "",
    }
