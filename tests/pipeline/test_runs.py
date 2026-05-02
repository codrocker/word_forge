"""StageRunStore: write stage_runs + atomically bump batches.total_cost_usd."""

from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa

from wordforge.pipeline.runs import StageRunStore


def _seed_batch(engine: sa.engine.Engine, batch_id: str, cap: float | None = None) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.batches (id, label, budget_cap_usd) VALUES (:id, :id, :cap)"
            ),
            {"id": batch_id, "cap": cap},
        )


def _batch_total(engine: sa.engine.Engine, batch_id: str) -> Decimal:
    with engine.connect() as conn:
        return conn.execute(
            sa.text("SELECT total_cost_usd FROM pipeline.batches WHERE id = :id"),
            {"id": batch_id},
        ).scalar()


def test_record_ok_writes_row_and_bumps_total(at_head):
    _seed_batch(at_head, "B1")
    store = StageRunStore(at_head)
    store.record_ok(
        batch_id="B1",
        word_id=1,
        stage_name="paraphrase",
        model="claude-opus-4",
        tokens_in=100,
        tokens_out=200,
        cost_usd=0.0042,
        duration_ms=1500,
    )
    with at_head.connect() as conn:
        row = (
            conn.execute(
                sa.text(
                    "SELECT status, model, tokens_input, tokens_output, cost_usd, "
                    "       duration_ms, error FROM pipeline.stage_runs "
                    "WHERE word_id = 1 AND stage_name = 'paraphrase'"
                )
            )
            .mappings()
            .first()
        )
    assert row["status"] == "ok"
    assert row["model"] == "claude-opus-4"
    assert row["tokens_input"] == 100
    assert row["tokens_output"] == 200
    assert float(row["cost_usd"]) == 0.0042
    assert row["duration_ms"] == 1500
    assert row["error"] is None
    assert float(_batch_total(at_head, "B1")) == 0.0042


def test_record_ok_accumulates_across_calls(at_head):
    _seed_batch(at_head, "B1")
    store = StageRunStore(at_head)
    for _ in range(3):
        store.record_ok(
            batch_id="B1",
            word_id=1,
            stage_name="paraphrase",
            model="m",
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.01,
            duration_ms=1,
        )
    assert float(_batch_total(at_head, "B1")) == 0.03


def test_record_failed_writes_error_and_does_not_bump(at_head):
    _seed_batch(at_head, "B1")
    store = StageRunStore(at_head)
    store.record_failed(
        batch_id="B1",
        word_id=1,
        stage_name="paraphrase",
        error="rate limited by provider",
    )
    assert float(_batch_total(at_head, "B1")) == 0.0
    with at_head.connect() as conn:
        row = (
            conn.execute(sa.text("SELECT status, error FROM pipeline.stage_runs WHERE word_id = 1"))
            .mappings()
            .first()
        )
    assert row["status"] == "failed"
    assert row["error"] == "rate limited by provider"


def test_record_ok_with_no_batch_id_still_writes(at_head):
    """stage_runs.batch_id is nullable; ad-hoc single-word runs don't need a batch.

    Spec §4 line 234 comment: word_id 故意不声明 FK（审计保留）；batch_id 本身
    NULLABLE 没有 CHECK NOT NULL。Ad-hoc call without batch_id must not attempt
    to UPDATE pipeline.batches.
    """
    store = StageRunStore(at_head)
    store.record_ok(
        batch_id=None,
        word_id=99,
        stage_name="paraphrase",
        model="m",
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.5,
        duration_ms=1,
    )
    with at_head.connect() as conn:
        row = (
            conn.execute(
                sa.text("SELECT status, cost_usd FROM pipeline.stage_runs WHERE word_id = 99")
            )
            .mappings()
            .first()
        )
    assert row["status"] == "ok"
    assert float(row["cost_usd"]) == 0.5


def test_record_ok_handles_null_initial_total(at_head):
    """Round 3 R3-codex-1: if a batch row has total_cost_usd=NULL (DDL
    allows it), `total + :cost` in PG would propagate NULL. COALESCE
    keeps accumulation correct."""
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.batches (id, label, total_cost_usd) "
                "VALUES ('B-null', 'B-null', NULL)"
            )
        )
    store = StageRunStore(at_head)
    store.record_ok(
        batch_id="B-null",
        word_id=1,
        stage_name="paraphrase",
        model="m",
        tokens_in=1,
        tokens_out=1,
        cost_usd=0.5,
        duration_ms=1,
    )
    with at_head.connect() as conn:
        total = conn.execute(
            sa.text("SELECT total_cost_usd FROM pipeline.batches WHERE id='B-null'")
        ).scalar()
    assert float(total) == 0.5


def test_record_ok_unknown_batch_raises_and_rolls_back(at_head):
    """Round 1 U-codex-1: typo'd batch_id must fail loud; the stage_runs
    row must roll back inside the same transaction (no orphaned run row).
    """
    import pytest

    store = StageRunStore(at_head)
    with pytest.raises(LookupError, match="B-typo"):
        store.record_ok(
            batch_id="B-typo",
            word_id=1,
            stage_name="paraphrase",
            model="m",
            tokens_in=1,
            tokens_out=1,
            cost_usd=0.5,
            duration_ms=1,
        )
    with at_head.connect() as conn:
        n = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.stage_runs WHERE word_id = 1")
        ).scalar()
    assert n == 0, "stage_runs row must be rolled back when batch UPDATE affects 0 rows"
