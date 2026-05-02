"""P5b Task 2: DerivativesStage using a stub completer."""

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
from wordforge.stages.derivatives import DerivativesStage

_STUB_RESPONSE = (
    '{"word_forms":{"plural":"apples"},'
    '"per_meaning":[{"meaning_index":0,"synonyms":["fruit"],"antonyms":[]}]}'
)


def _stub_completer(*, model, prompt, **kw):
    return LLMCompletion(
        response={"text": _STUB_RESPONSE},
        cost_usd=0.002,
    )


@pytest.fixture
def stage(at_head):
    engine = make_engine()
    artifacts = StageArtifactStore(engine)
    cache = CacheStore(engine)
    cfg = StageConfig(
        parser_version="1", prompt_version="v1", model="claude-opus-4", cost_estimate_usd=0.002
    )
    llm = LLMClient(store=cache, completers={"anthropic": _stub_completer})
    return (
        DerivativesStage(engine=engine, artifacts=artifacts, config=cfg, llm=llm),
        engine,
        artifacts,
    )


def test_derivatives_fingerprint_deterministic(stage):
    s, engine, artifacts = stage
    ingest_words(engine, raw_forms=["apple"], batch_id="B_D1")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_D1'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="para_fp",
        payload={"meanings": [{"pos": "n", "cn": "苹果"}]},
        source="pipeline:anthropic:claude-opus-4:paraphrase_v1",
    )
    fp1 = s.expected_fingerprint(word_id=wid)
    fp2 = s.expected_fingerprint(word_id=wid)
    assert fp1 == fp2


def test_derivatives_run_one_returns_word_forms(stage):
    s, engine, artifacts = stage
    ingest_words(engine, raw_forms=["apple"], batch_id="B_D2")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_D2'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="para_fp",
        payload={"meanings": [{"pos": "n", "cn": "苹果"}]},
        source="pipeline:anthropic:claude-opus-4:paraphrase_v1",
    )
    payload = asyncio.run(s.run_one(word_id=wid))
    assert payload.payload["word_forms"]["plural"] == "apples"
    assert payload.source == "pipeline:anthropic:claude-opus-4:derivatives_v1"
    assert payload.cost_usd == 0.002


def test_derivatives_missing_upstream_raises(stage):
    s, engine, _ = stage
    ingest_words(engine, raw_forms=["ghost"], batch_id="B_D3")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_D3'")
        ).scalar_one()
    with pytest.raises(LookupError, match="paraphrase missing"):
        asyncio.run(s.run_one(word_id=wid))
