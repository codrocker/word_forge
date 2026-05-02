"""P4: `wordforge plan` dry-run backend.

`build_plan` is a pure read-only query. Does not touch stage_runs,
external_call_cache, or any writable table. Safe to run anytime.

Semantic contract (Round 1 D2/D4):
- `needs_rerun`   = words with NO stage_artifacts row for (word_id, stage_name)
                    in the scope (batch or all). Fingerprint drift detection
                    is upgraded to fingerprint-aware in the first task of P5.
- `has_artifact`  = words WITH a stage_artifacts row for that pair; DOES NOT
                    verify fingerprint freshness in P4.
- `estimated_cost_usd` = needs_rerun * config.cost_estimate_usd.
- `sample_forms`  = up to 10 normalized_form strings that still need rerun
                    (spec S7 L501 "list words to rerun").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import sqlalchemy as sa
from sqlalchemy.engine import Engine

from wordforge.config import WordforgeConfig

_SAMPLE_LIMIT = 10


@dataclass(frozen=True)
class PlanReport:
    stage_name: str
    batch_id: str | None
    total_candidates: int
    needs_rerun: int
    has_artifact: int
    estimated_cost_usd: float
    cost_source: Literal["config"]
    sample_forms: tuple[str, ...]


def build_plan(
    engine: Engine,
    *,
    config: WordforgeConfig,
    stage_name: str,
    batch_id: str | None,
) -> PlanReport:
    if stage_name not in config.stages:
        raise ValueError(
            f"unknown stage: {stage_name!r}; configured: {sorted(config.stages.keys())}"
        )
    stage_cfg = config.stages[stage_name]

    with engine.connect() as conn:
        if batch_id is not None:
            exists = conn.execute(
                sa.text("SELECT 1 FROM pipeline.batches WHERE id = :b"),
                {"b": batch_id},
            ).scalar()
            if exists is None:
                raise LookupError(f"unknown batch: {batch_id!r}")

        if batch_id is None:
            total = conn.execute(sa.text("SELECT count(*) FROM pipeline.words")).scalar_one()
        else:
            total = conn.execute(
                sa.text("SELECT count(*) FROM pipeline.words WHERE batch_id = :b"),
                {"b": batch_id},
            ).scalar_one()

        if batch_id is None:
            has_artifact = conn.execute(
                sa.text(
                    "SELECT count(*) FROM pipeline.words w "
                    "JOIN pipeline.stage_artifacts a "
                    "  ON a.word_id = w.id AND a.stage_name = :s"
                ),
                {"s": stage_name},
            ).scalar_one()
        else:
            has_artifact = conn.execute(
                sa.text(
                    "SELECT count(*) FROM pipeline.words w "
                    "JOIN pipeline.stage_artifacts a "
                    "  ON a.word_id = w.id AND a.stage_name = :s "
                    "WHERE w.batch_id = :b"
                ),
                {"s": stage_name, "b": batch_id},
            ).scalar_one()

        if batch_id is None:
            sample_rows = conn.execute(
                sa.text(
                    "SELECT w.normalized_form FROM pipeline.words w "
                    "LEFT JOIN pipeline.stage_artifacts a "
                    "  ON a.word_id = w.id AND a.stage_name = :s "
                    "WHERE a.word_id IS NULL "
                    "ORDER BY w.normalized_form "
                    "LIMIT :lim"
                ),
                {"s": stage_name, "lim": _SAMPLE_LIMIT},
            ).all()
        else:
            sample_rows = conn.execute(
                sa.text(
                    "SELECT w.normalized_form FROM pipeline.words w "
                    "LEFT JOIN pipeline.stage_artifacts a "
                    "  ON a.word_id = w.id AND a.stage_name = :s "
                    "WHERE a.word_id IS NULL AND w.batch_id = :b "
                    "ORDER BY w.normalized_form "
                    "LIMIT :lim"
                ),
                {"s": stage_name, "b": batch_id, "lim": _SAMPLE_LIMIT},
            ).all()
        sample_forms = tuple(r[0] for r in sample_rows)

    needs_rerun = int(total) - int(has_artifact)
    return PlanReport(
        stage_name=stage_name,
        batch_id=batch_id,
        total_candidates=int(total),
        needs_rerun=needs_rerun,
        has_artifact=int(has_artifact),
        estimated_cost_usd=needs_rerun * stage_cfg.cost_estimate_usd,
        cost_source="config",
        sample_forms=sample_forms,
    )
