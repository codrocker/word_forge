"""Unit tests for scripts/replicate/field_mapping.py.

Pure functions: PG row dict + relations context -> MySQL row dict.
No real DB.
"""

from __future__ import annotations

import json

from scripts.replicate.field_mapping import row_to_mysql_word


def test_word_basic_fields_direct_passthrough():
    pg_row = {
        "word_id": 100001,
        "type": 1,
        "form": "the",
        "phonetic_us": "ðə",
        "phonetic_uk": "ðə",
        "audio_us": "https://cdn/us/the.mp3",
        "audio_uk": None,
        "source": "pipeline:local:export_v1",
    }
    mysql_row = row_to_mysql_word(pg_row, meaning_ids=[], mnemonic_ids=[], phrase_ids=[])

    assert mysql_row["word_id"] == 100001
    assert mysql_row["type"] == 1
    assert mysql_row["form"] == "the"
    assert mysql_row["phonetic_us"] == "ðə"
    assert mysql_row["audio_uk"] is None
    assert mysql_row["source"] == "pipeline:local:export_v1"


def test_word_status_hardcoded_to_1():
    pg_row = {
        "word_id": 100001, "type": 1, "form": "x",
        "phonetic_us": "", "phonetic_uk": "",
        "audio_us": None, "audio_uk": None, "source": None,
    }
    row = row_to_mysql_word(pg_row, meaning_ids=[], mnemonic_ids=[], phrase_ids=[])
    assert row["status"] == 1


def test_word_phonetic_null_becomes_empty_string():
    pg_row = {
        "word_id": 100001, "type": 1, "form": "x",
        "phonetic_us": None, "phonetic_uk": None,
        "audio_us": None, "audio_uk": None, "source": None,
    }
    row = row_to_mysql_word(pg_row, meaning_ids=[], mnemonic_ids=[], phrase_ids=[])
    assert row["phonetic_us"] == ""
    assert row["phonetic_uk"] == ""


def test_word_meanings_composed_as_object_array():
    pg_row = {
        "word_id": 100001, "type": 1, "form": "x",
        "phonetic_us": "", "phonetic_uk": "",
        "audio_us": None, "audio_uk": None, "source": None,
    }
    row = row_to_mysql_word(
        pg_row, meaning_ids=[200001, 200002], mnemonic_ids=[300001], phrase_ids=[],
    )
    assert json.loads(row["meanings"]) == [{"id": 200001}, {"id": 200002}]
    assert json.loads(row["mnemonics"]) == [{"id": 300001}]
    assert row["phrases"] is None


def test_word_nulls_per_wiki_tmp_null_list():
    pg_row = {
        "word_id": 100001, "type": 1, "form": "x",
        "phonetic_us": "", "phonetic_uk": "",
        "audio_us": None, "audio_uk": None, "source": None,
    }
    row = row_to_mysql_word(pg_row, meaning_ids=[], mnemonic_ids=[], phrase_ids=[])
    for col in ("plural", "comparative", "superlative", "structure",
                "third_person", "present_participle", "past_tense", "past_participle",
                "derivatives", "morpheme_derivatives", "family", "base"):
        assert row[col] is None, f"{col} must be None"
