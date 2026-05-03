"""Unit tests for scripts/replicate/field_mapping.py.

Pure functions: PG row dict + relations context -> MySQL row dict.
No real DB.
"""

from __future__ import annotations

import json

from scripts.replicate.field_mapping import (
    row_to_mysql_meaning,
    row_to_mysql_mnemonic,
    row_to_mysql_phrase,
    row_to_mysql_sentence,
    row_to_mysql_word,
)


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


# --- meaning ---


def test_meaning_pos_direct_passthrough():
    """wordforge _POS_MAP 已按 wiki 枚举,镜像直传不重映射."""
    pg = {
        "meaning_id": 200001, "word_id": 100001, "pos": 3,
        "cn_paraphrase": "的", "en_paraphrase": "adj",
        "equivalents": ["的", "这个"],
        "synonyms": None, "antonyms": None,
        "phonetic_us": None, "audio_us": None, "phonetic_uk": None, "audio_uk": None,
        "source": "pipeline:stages.paraphrase",
    }
    row = row_to_mysql_meaning(pg, sentence_ids=[400001, 400002])
    assert row["meaning_id"] == 200001
    assert row["pos"] == 3
    assert row["pos_sub"] is None
    assert row["user_group"] is None
    assert json.loads(row["equivalents"]) == ["的", "这个"]
    assert json.loads(row["sentences"]) == [{"sentence_id": 400001}, {"sentence_id": 400002}]
    assert row["synonyms"] is None
    assert row["source"] == "pipeline:stages.paraphrase"


def test_meaning_empty_sentence_list_null():
    pg = {
        "meaning_id": 200001, "word_id": 100001, "pos": None,
        "cn_paraphrase": "x", "en_paraphrase": None,
        "equivalents": None, "synonyms": None, "antonyms": None,
        "phonetic_us": None, "audio_us": None, "phonetic_uk": None, "audio_uk": None,
        "source": None,
    }
    row = row_to_mysql_meaning(pg, sentence_ids=[])
    assert row["sentences"] is None


# --- sentence ---


def test_sentence_direct_passthrough_audio_nulled():
    """audio_us / audio_uk 按 wiki 临时设置 NULL."""
    pg = {
        "sentence_id": 400001, "word_id": 100001, "meaning_id": 200001,
        "form": "Six of the 38 people were U.S. citizens.",
        "translation": "那 38 人中有 6 个是美国公民.",
        "highlight": [[4, 7]],
        "source": "pipeline:stages.examples",
    }
    row = row_to_mysql_sentence(pg)
    assert row["sentence_id"] == 400001
    assert row["form"].startswith("Six")
    assert row["audio_us"] is None
    assert row["audio_uk"] is None
    assert row["user_group"] is None
    assert row["citation"] is None
    assert row["citation_detail"] is None
    assert json.loads(row["highlight"]) == [[4, 7]]


def test_sentence_highlight_none_stays_none():
    pg = {
        "sentence_id": 400001, "word_id": 100001, "meaning_id": 200001,
        "form": "x", "translation": "y", "highlight": None, "source": None,
    }
    row = row_to_mysql_sentence(pg)
    assert row["highlight"] is None


# --- mnemonic ---


def test_mnemonic_content_jsonb_passthrough():
    """wordforge mnemonic.content 是 JSONB,直接 json.dumps;creator_id=0 占位."""
    pg = {
        "mnemonic_id": 500001, "word_id": 100001, "type": 1,
        "content": {"kind": "phonetic", "text": "因为在里面,所以说 in."},
        "source": "LLM:claude_sonnet_4_5_thinking",
    }
    row = row_to_mysql_mnemonic(pg)
    assert row["mnemonic_id"] == 500001
    assert row["type"] == 1
    assert row["user_group"] == 0
    assert row["creator_id"] == 0
    assert row["source"] == "LLM:claude_sonnet_4_5_thinking"
    parsed = json.loads(row["content"])
    assert parsed == {"kind": "phonetic", "text": "因为在里面,所以说 in."}


# --- phrase ---


def test_phrase_direct_passthrough():
    pg = {
        "phrase_id": 600001,
        "form": "take off",
        "meaning": "起飞; 脱下",
        "audio_us": "https://cdn/us/take-off.mp3",
        "audio_uk": "https://cdn/uk/take-off.mp3",
    }
    row = row_to_mysql_phrase(pg)
    assert row["phrase_id"] == 600001
    assert row["form"] == "take off"
    assert row["meaning"].startswith("起飞")


def test_phrase_missing_audio_empty_string():
    """wiki: phrase.audio_us / audio_uk NOT NULL.若 PG 无,填 ''."""
    pg = {"phrase_id": 600001, "form": "x", "meaning": "y", "audio_us": None, "audio_uk": None}
    row = row_to_mysql_phrase(pg)
    assert row["audio_us"] == ""
    assert row["audio_uk"] == ""
