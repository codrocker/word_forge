"""P5b Task 4: MnemonicStage using a stub completer."""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from wordforge.cache import CacheStore
from wordforge.config import StageConfig
from wordforge.db.engine import make_engine
from wordforge.ingest import ingest_words
from wordforge.llm.client import LLMClient, LLMCompletion
from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.stages.mnemonic import MnemonicStage


def _stub_completer(*, model, prompt, **kw):
    return LLMCompletion(
        response={"text": '{"mnemonic":"Apple谐音阿婆，阿婆在卖苹果","kind":"phonetic"}'},
        cost_usd=0.003,
    )


@pytest.fixture
def stage(at_head):
    engine = make_engine()
    artifacts = StageArtifactStore(engine)
    cache = CacheStore(engine)
    cfg = StageConfig(
        parser_version="1", prompt_version="v1", model="claude-opus-4", cost_estimate_usd=0.003
    )
    llm = LLMClient(store=cache, completers={"anthropic": _stub_completer})
    return (
        MnemonicStage(engine=engine, artifacts=artifacts, config=cfg, llm=llm),
        engine,
        artifacts,
    )


def test_mnemonic_fingerprint_deterministic(stage):
    s, engine, artifacts = stage
    ingest_words(engine, raw_forms=["apple"], batch_id="B_M1")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_M1'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="para_fp",
        payload={"meanings": [{"pos": "n", "cn": "苹果"}]},
        source="pipeline:anthropic:claude-opus-4:paraphrase_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="phonetic",
        fingerprint="phon_fp",
        payload={"phonetic_us": "ˈæpl", "phonetic_uk": "ˈæpl", "audio_us": None, "audio_uk": None},
        source="pipeline:local:phonetic_v1",
    )
    fp1 = s.expected_fingerprint(word_id=wid)
    fp2 = s.expected_fingerprint(word_id=wid)
    assert fp1 == fp2


def test_mnemonic_run_one_returns_mnemonic(stage):
    s, engine, artifacts = stage
    ingest_words(engine, raw_forms=["apple"], batch_id="B_M2")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_M2'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="para_fp",
        payload={"meanings": [{"pos": "n", "cn": "苹果"}]},
        source="pipeline:anthropic:claude-opus-4:paraphrase_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="phonetic",
        fingerprint="phon_fp",
        payload={"phonetic_us": "ˈæpl", "phonetic_uk": "ˈæpl", "audio_us": None, "audio_uk": None},
        source="pipeline:local:phonetic_v1",
    )
    payload = asyncio.run(s.run_one(word_id=wid))
    assert "mnemonic" in payload.payload
    assert payload.payload["kind"] == "phonetic"
    assert payload.source == "pipeline:anthropic:claude-opus-4:mnemonic_v1"
    assert payload.cost_usd == 0.003


def test_mnemonic_missing_upstream_raises(stage):
    s, engine, _ = stage
    ingest_words(engine, raw_forms=["ghost"], batch_id="B_M3")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_M3'")
        ).scalar_one()
    with pytest.raises(LookupError, match="paraphrase missing"):
        asyncio.run(s.run_one(word_id=wid))
