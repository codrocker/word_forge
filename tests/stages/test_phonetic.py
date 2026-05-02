"""P5a Task 2: PhoneticStage parses fetch_dict raw_json for IPA + audio URLs."""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from wordforge.config import StageConfig
from wordforge.db.engine import make_engine
from wordforge.ingest import ingest_words
from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.stages.phonetic import PhoneticStage, parse_phonetics

# Mirrors real Youdao jsonapi shape (subset).
_APPLE_JSON = {
    "simple": {
        "word": [
            {
                "ukphone": "ˈæp(ə)l",
                "usphone": "ˈæp(ə)l",
                "ukspeech": "apple&type=1",
                "usspeech": "apple&type=2",
            }
        ]
    }
}


def test_parse_phonetics_extracts_both_ipa():
    r = parse_phonetics(_APPLE_JSON)
    assert r["phonetic_uk"] == "ˈæp(ə)l"
    assert r["phonetic_us"] == "ˈæp(ə)l"


def test_parse_phonetics_composes_full_audio_urls():
    r = parse_phonetics(_APPLE_JSON)
    assert r["audio_uk"] == "https://dict.youdao.com/dictvoice?audio=apple&type=1"
    assert r["audio_us"] == "https://dict.youdao.com/dictvoice?audio=apple&type=2"


def test_parse_phonetics_returns_none_for_missing_fields():
    r = parse_phonetics({})
    assert r["phonetic_uk"] is None
    assert r["phonetic_us"] is None
    assert r["audio_uk"] is None
    assert r["audio_us"] is None


def test_parse_phonetics_falls_back_to_ec_word():
    """When `simple` is absent, `ec.word[0]` carries the same keys."""
    r = parse_phonetics(
        {"ec": {"word": [{"usphone": "rʌn", "ukphone": "rʌn", "usspeech": "run&type=2"}]}}
    )
    assert r["phonetic_us"] == "rʌn"
    assert r["audio_us"].endswith("run&type=2")


@pytest.fixture
def stage(at_head):
    engine = make_engine()
    artifacts = StageArtifactStore(engine)
    cfg = StageConfig(parser_version="1", cost_estimate_usd=0.0)
    return PhoneticStage(engine=engine, artifacts=artifacts, config=cfg), engine, artifacts


def test_phonetic_run_one_reads_fetch_dict_upstream(stage):
    s, engine, artifacts = stage
    ingest_words(engine, raw_forms=["apple"], batch_id="B_PHON")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_PHON'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="fetch_dict",
        fingerprint="fd_fp",
        payload={"raw_json": _APPLE_JSON, "form": "apple"},
        source="pipeline:youdao:fetch_dict_v1",
    )
    payload = asyncio.run(s.run_one(word_id=wid))
    assert payload.payload["phonetic_uk"] == "ˈæp(ə)l"
    assert payload.source.startswith("pipeline:local:phonetic_v")


def test_phonetic_raises_when_upstream_missing(stage):
    s, engine, _ = stage
    ingest_words(engine, raw_forms=["ghost"], batch_id="B_MISS")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_MISS'")
        ).scalar_one()
    with pytest.raises(LookupError, match="fetch_dict artifact missing"):
        asyncio.run(s.run_one(word_id=wid))
