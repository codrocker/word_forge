"""P5b Task 1: ParaphraseStage using a stub completer."""

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
from wordforge.stages.paraphrase import ParaphraseStage


def _stub_completer(*, model, prompt, **kw):
    return LLMCompletion(
        response={"text": '{"meanings":[{"pos":"n","cn":"苹果","en":"apple"}]}'},
        cost_usd=0.004,
    )


@pytest.fixture
def stage(at_head):
    engine = make_engine()
    artifacts = StageArtifactStore(engine)
    cache = CacheStore(engine)
    cfg = StageConfig(
        parser_version="1", prompt_version="v1", model="claude-opus-4", cost_estimate_usd=0.004
    )
    llm = LLMClient(store=cache, completers={"anthropic": _stub_completer})
    s = ParaphraseStage(engine=engine, artifacts=artifacts, config=cfg, llm=llm)
    return s, engine, artifacts


def test_paraphrase_fingerprint_deterministic(stage):
    s, engine, artifacts = stage
    ingest_words(engine, raw_forms=["apple"], batch_id="B_P1")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_P1'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="fetch_dict",
        fingerprint="fd_fp",
        payload={"raw_json": {"simple": {"word": [{"usphone": "ˈæp(ə)l"}]}}},
        source="pipeline:youdao:fetch_dict_v1",
    )
    fp1 = s.expected_fingerprint(word_id=wid)
    fp2 = s.expected_fingerprint(word_id=wid)
    assert fp1 == fp2


def test_paraphrase_run_one_returns_meanings(stage):
    s, engine, artifacts = stage
    ingest_words(engine, raw_forms=["apple"], batch_id="B_P2")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_P2'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="fetch_dict",
        fingerprint="fd",
        payload={"raw_json": {"simple": {"word": [{"usphone": "ˈæp(ə)l"}]}}},
        source="pipeline:youdao:fetch_dict_v1",
    )
    payload = asyncio.run(s.run_one(word_id=wid))
    assert payload.payload["meanings"][0]["cn"] == "苹果"
    assert payload.source == "pipeline:anthropic:claude-opus-4:paraphrase_v1"
    assert payload.cost_usd == 0.004


def test_paraphrase_missing_upstream_raises(stage):
    s, engine, _ = stage
    ingest_words(engine, raw_forms=["ghost"], batch_id="B_P3")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_P3'")
        ).scalar_one()
    with pytest.raises(LookupError, match="fetch_dict missing"):
        asyncio.run(s.run_one(word_id=wid))


def test_paraphrase_records_tokens_and_duration(at_head):
    """Stub completer returns in_tok/out_tok — assert they flow to StagePayload."""
    from wordforge.cache import CacheStore
    from wordforge.config import StageConfig
    from wordforge.db.engine import make_engine
    from wordforge.llm.client import LLMClient, LLMCompletion
    from wordforge.pipeline.artifacts import StageArtifactStore
    from wordforge.stages.paraphrase import ParaphraseStage

    def _completer_with_tokens(*, model, prompt, **kw):
        return LLMCompletion(
            response={
                "text": '{"meanings":[{"pos":"n","cn":"苹果","en":"apple"}]}',
                "in_tok": 100,
                "out_tok": 200,
            },
            cost_usd=0.004,
        )

    engine = make_engine()
    artifacts = StageArtifactStore(engine)
    cache = CacheStore(engine)
    cfg = StageConfig(
        parser_version="1", prompt_version="v1", model="claude-opus-4", cost_estimate_usd=0.004
    )
    llm = LLMClient(store=cache, completers={"anthropic": _completer_with_tokens})
    s = ParaphraseStage(engine=engine, artifacts=artifacts, config=cfg, llm=llm)

    ingest_words(engine, raw_forms=["apple"], batch_id="B_P_TOK")
    with engine.begin() as conn:
        wid = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id='B_P_TOK'")
        ).scalar_one()
    artifacts.upsert(
        word_id=wid,
        stage_name="fetch_dict",
        fingerprint="fd",
        payload={"raw_json": {"simple": {"word": [{"usphone": "ˈæp(ə)l"}]}}},
        source="pipeline:youdao:fetch_dict_v1",
    )
    payload = asyncio.run(s.run_one(word_id=wid))
    assert payload.tokens_in == 100
    assert payload.tokens_out == 200
    assert payload.duration_ms is not None
    assert payload.duration_ms >= 0
    engine.dispose()
