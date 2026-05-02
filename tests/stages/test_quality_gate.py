"""P5c Task 0: QualityGateStage — rule-based validation tests."""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from wordforge.config import StageConfig
from wordforge.db.engine import make_engine
from wordforge.ingest import ingest_words
from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.stages.quality_gate import QualityGateStage


def _make_stage(engine):
    artifacts = StageArtifactStore(engine)
    cfg = StageConfig(parser_version="1", cost_estimate_usd=0.0)
    return QualityGateStage(engine=engine, artifacts=artifacts, config=cfg), artifacts


def _seed_word(engine, form="apple", batch_id="B_QG"):
    ingest_words(engine, raw_forms=[form], batch_id=batch_id)
    with engine.begin() as conn:
        return conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id = :b"),
            {"b": batch_id},
        ).scalar_one()


def _seed_upstreams(
    artifacts, wid, *, meanings=None, examples=None, mnemonic=None, derivatives=None
):
    """Seed all 4 upstream artifacts with sensible defaults."""
    if meanings is None:
        meanings = [{"pos": "n", "cn": "苹果", "en": "a fruit"}]
    if examples is None:
        examples = {"per_meaning": [{"meaning_index": 0, "examples": [{"en": "I ate an apple."}]}]}
    if mnemonic is None:
        mnemonic = {"mnemonic": "Apple谐音阿婆", "kind": "phonetic"}
    if derivatives is None:
        derivatives = {"word_forms": {}, "per_meaning": []}

    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="fp_para",
        payload={"meanings": meanings},
        source="pipeline:anthropic:claude-opus-4:paraphrase_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="derivatives",
        fingerprint="fp_deriv",
        payload=derivatives,
        source="pipeline:anthropic:claude-opus-4:derivatives_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="examples",
        fingerprint="fp_ex",
        payload=examples,
        source="pipeline:anthropic:claude-opus-4:examples_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="mnemonic",
        fingerprint="fp_mnem",
        payload=mnemonic,
        source="pipeline:anthropic:claude-opus-4:mnemonic_v1",
    )


def test_quality_gate_happy_path(at_head):
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_QG1")
    _seed_upstreams(artifacts, wid)

    result = asyncio.run(stage.run_one(word_id=wid))
    assert result.payload["passed"] is True
    assert result.payload["failed_rules"] == []
    assert "checked_at" in result.payload
    assert result.source == "pipeline:local:quality_gate_v1"
    assert result.cost_usd == 0.0


def test_quality_gate_meanings_non_empty(at_head):
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_QG2")
    _seed_upstreams(artifacts, wid, meanings=[])

    result = asyncio.run(stage.run_one(word_id=wid))
    assert result.payload["passed"] is False
    assert any("meanings_non_empty" in r for r in result.payload["failed_rules"])


def test_quality_gate_each_meaning_has_cn(at_head):
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_QG3")
    _seed_upstreams(artifacts, wid, meanings=[{"pos": "n", "cn": ""}, {"pos": "v", "cn": "吃"}])

    result = asyncio.run(stage.run_one(word_id=wid))
    assert result.payload["passed"] is False
    assert any("each_meaning_has_cn: meaning[0]" in r for r in result.payload["failed_rules"])


def test_quality_gate_each_meaning_has_pos(at_head):
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_QG4")
    _seed_upstreams(artifacts, wid, meanings=[{"cn": "苹果"}])

    result = asyncio.run(stage.run_one(word_id=wid))
    assert result.payload["passed"] is False
    assert any("each_meaning_has_pos" in r for r in result.payload["failed_rules"])


def test_quality_gate_examples_coverage(at_head):
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_QG5")
    _seed_upstreams(
        artifacts, wid, examples={"per_meaning": [{"meaning_index": 0, "examples": []}]}
    )

    result = asyncio.run(stage.run_one(word_id=wid))
    assert result.payload["passed"] is False
    assert any("examples_coverage" in r for r in result.payload["failed_rules"])


def test_quality_gate_mnemonic_present(at_head):
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_QG6")
    _seed_upstreams(artifacts, wid, mnemonic={"mnemonic": "", "kind": "phonetic"})

    result = asyncio.run(stage.run_one(word_id=wid))
    assert result.payload["passed"] is False
    assert any("mnemonic_present" in r for r in result.payload["failed_rules"])


def test_quality_gate_fingerprint_deterministic(at_head):
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_QG7")
    _seed_upstreams(artifacts, wid)

    fp1 = stage.expected_fingerprint(word_id=wid)
    fp2 = stage.expected_fingerprint(word_id=wid)
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex


def test_quality_gate_missing_upstream_raises(at_head):
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_QG8")
    # Only seed paraphrase — missing derivatives, examples, mnemonic
    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="fp_para",
        payload={"meanings": [{"pos": "n", "cn": "苹果"}]},
        source="pipeline:anthropic:claude-opus-4:paraphrase_v1",
    )

    with pytest.raises(LookupError, match="derivatives artifact missing"):
        asyncio.run(stage.run_one(word_id=wid))
