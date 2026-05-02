"""BudgetGate: per-stage startup-time cap check (spec §6 "Budget 熔断")."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from wordforge.pipeline.budget import BudgetExceeded, BudgetGate


def _seed(engine, batch_id, *, cap, total=0.0):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.batches "
                "(id, label, total_cost_usd, budget_cap_usd) "
                "VALUES (:id, :id, :total, :cap)"
            ),
            {"id": batch_id, "total": total, "cap": cap},
        )


def test_gate_passes_when_under_cap(at_head):
    _seed(at_head, "B1", cap=10.0, total=3.0)
    BudgetGate(at_head).check("B1")  # should not raise


def test_gate_passes_when_cap_is_null(at_head):
    """No cap configured = no budget enforcement."""
    _seed(at_head, "B1", cap=None, total=9999.0)
    BudgetGate(at_head).check("B1")  # no raise


def test_gate_blocks_when_at_or_over_cap(at_head):
    _seed(at_head, "B1", cap=5.0, total=5.0)
    with pytest.raises(BudgetExceeded, match="B1"):
        BudgetGate(at_head).check("B1")


def test_gate_raises_when_batch_unknown(at_head):
    with pytest.raises(LookupError, match="unknown batch"):
        BudgetGate(at_head).check("does-not-exist")


def test_gate_with_none_batch_is_noop(at_head):
    BudgetGate(at_head).check(None)  # ad-hoc runs: no batch, no gate
