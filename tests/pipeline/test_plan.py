"""P4 Task 2: plan.build_plan — dry-run cost + rerun candidates.

Round 1 D6: seed via `ingest_words` (production path) rather than hand-written
INSERT so normalize(casefold) / type inference stays consistent.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from wordforge.config import StageConfig, WordforgeConfig
from wordforge.db.engine import make_engine
from wordforge.ingest import ingest_words
from wordforge.pipeline.plan import PlanReport, build_plan


def _seed_batch_and_words(engine, batch_id: str, words: list[str]) -> list[int]:
    ingest_words(engine, raw_forms=words, batch_id=batch_id)
    with engine.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id = :b ORDER BY normalized_form"),
            {"b": batch_id},
        ).all()
    return [r[0] for r in rows]


def _seed_artifact(engine, *, word_id: int, stage_name: str, fingerprint: str = "fp_fake") -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.stage_artifacts "
                "(word_id, stage_name, fingerprint, payload, source) "
                "VALUES (:w, :s, :fp, CAST('{}' AS jsonb), 'test')"
            ),
            {"w": word_id, "s": stage_name, "fp": fingerprint},
        )


def _cfg(cost_per_word: float = 0.004) -> WordforgeConfig:
    return WordforgeConfig(
        stages={
            "paraphrase": StageConfig(
                parser_version="1",
                prompt_version="v1",
                model="claude-opus-4",
                cost_estimate_usd=cost_per_word,
            ),
            "fetch_dict": StageConfig(parser_version="1", cost_estimate_usd=0.0),
        },
        default_budget_cap_usd=None,
    )


def test_build_plan_all_new_words(at_head):
    engine = make_engine()
    _seed_batch_and_words(engine, "B1", ["apple", "banana", "cherry"])
    report = build_plan(engine, config=_cfg(0.004), stage_name="paraphrase", batch_id="B1")
    assert isinstance(report, PlanReport)
    assert report.stage_name == "paraphrase"
    assert report.batch_id == "B1"
    assert report.total_candidates == 3
    assert report.needs_rerun == 3
    assert report.has_artifact == 0
    assert report.estimated_cost_usd == pytest.approx(0.012)
    assert report.cost_source == "config"
    assert set(report.sample_forms) == {"apple", "banana", "cherry"}


def test_build_plan_some_artifacts_exist(at_head):
    engine = make_engine()
    ids = _seed_batch_and_words(engine, "B2", ["a", "b", "c", "d"])
    _seed_artifact(engine, word_id=ids[0], stage_name="paraphrase")
    _seed_artifact(engine, word_id=ids[1], stage_name="paraphrase")
    report = build_plan(engine, config=_cfg(0.004), stage_name="paraphrase", batch_id="B2")
    assert report.total_candidates == 4
    assert report.has_artifact == 2
    assert report.needs_rerun == 2
    assert report.estimated_cost_usd == pytest.approx(0.008)
    assert set(report.sample_forms) == {"c", "d"}


def test_build_plan_artifact_for_other_stage_does_not_count(at_head):
    engine = make_engine()
    ids = _seed_batch_and_words(engine, "B3", ["a", "b"])
    _seed_artifact(engine, word_id=ids[0], stage_name="fetch_dict")
    report = build_plan(engine, config=_cfg(0.004), stage_name="paraphrase", batch_id="B3")
    assert report.has_artifact == 0
    assert report.needs_rerun == 2
    assert set(report.sample_forms) == {"a", "b"}


def test_build_plan_no_batch_filter_sees_all_words(at_head):
    engine = make_engine()
    _seed_batch_and_words(engine, "B4", ["a", "b"])
    _seed_batch_and_words(engine, "B5", ["c", "d", "e"])
    report = build_plan(engine, config=_cfg(0.004), stage_name="paraphrase", batch_id=None)
    assert report.total_candidates == 5
    assert report.batch_id is None
    assert set(report.sample_forms) == {"a", "b", "c", "d", "e"}


def test_build_plan_empty_batch(at_head):
    engine = make_engine()
    # Round 2 D8: ingest_words(raw_forms=[]) short-circuits before batch
    # INSERT — correct production semantics. Seed the batch directly here.
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO pipeline.batches (id, label) VALUES ('B6', 'B6')"),
        )
    report = build_plan(engine, config=_cfg(0.004), stage_name="paraphrase", batch_id="B6")
    assert report.total_candidates == 0
    assert report.needs_rerun == 0
    assert report.has_artifact == 0
    assert report.estimated_cost_usd == 0.0
    assert report.sample_forms == ()


def test_build_plan_unknown_stage_raises(at_head):
    engine = make_engine()
    _seed_batch_and_words(engine, "B7", ["a"])
    with pytest.raises(ValueError, match="unknown stage"):
        build_plan(engine, config=_cfg(), stage_name="no_such_stage", batch_id="B7")


def test_build_plan_unknown_batch_raises(at_head):
    engine = make_engine()
    with pytest.raises(LookupError, match="unknown batch"):
        build_plan(engine, config=_cfg(), stage_name="paraphrase", batch_id="DOES_NOT_EXIST")


def test_build_plan_cost_source_is_config(at_head):
    engine = make_engine()
    _seed_batch_and_words(engine, "B8", ["a"])
    report = build_plan(engine, config=_cfg(), stage_name="paraphrase", batch_id="B8")
    assert report.cost_source == "config"


def test_build_plan_sample_forms_capped_at_10(at_head):
    engine = make_engine()
    words = [f"w{i:02d}" for i in range(12)]
    _seed_batch_and_words(engine, "B9", words)
    report = build_plan(engine, config=_cfg(0.004), stage_name="paraphrase", batch_id="B9")
    assert report.total_candidates == 12
    assert report.needs_rerun == 12
    assert len(report.sample_forms) == 10
    assert all(f.startswith("w") for f in report.sample_forms)


# 10th test: explicit mid-batch scenario to verify has_artifact boundary.
def test_build_plan_mixed_has_artifact_and_missing(at_head):
    engine = make_engine()
    ids = _seed_batch_and_words(engine, "B10", ["x", "y", "z"])
    _seed_artifact(engine, word_id=ids[1], stage_name="paraphrase")  # only y has artifact
    report = build_plan(engine, config=_cfg(0.004), stage_name="paraphrase", batch_id="B10")
    assert report.total_candidates == 3
    assert report.has_artifact == 1
    assert report.needs_rerun == 2
    # Sample should NOT include "y" (it has artifact); only x and z.
    assert set(report.sample_forms) == {"x", "z"}
