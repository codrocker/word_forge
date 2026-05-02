"""BudgetGate — per-stage startup-time cap check.

Spec §6 Budget 熔断：每次 stage 启动前读 pipeline.batches.total_cost_usd；
如果 >= budget_cap_usd，抛 BudgetExceeded 让 runner 中止下一个 stage。

Intentionally coarse: ONE DB query per stage (not per-task). Spec §6 Round 5:
"per-task 加 SELECT 会引入 10 万次额外 DB 查询，不值得". Overshoot by at most
one full stage's worth of cost (paraphrase ≈ $50-200); configure
budget_cap_usd with that headroom in mind.

No crash-safe reservation (spec §10 #2). This is a tripwire, not a ledger.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Engine


class BudgetExceeded(RuntimeError):
    """Raised by BudgetGate.check when total_cost_usd >= budget_cap_usd."""


@dataclass
class BudgetGate:
    engine: Engine

    def check(self, batch_id: str | None) -> None:
        if batch_id is None:
            return  # ad-hoc single-word runs have no batch → no gate
        with self.engine.connect() as conn:
            row = (
                conn.execute(
                    sa.text(
                        "SELECT total_cost_usd, budget_cap_usd FROM pipeline.batches WHERE id = :id"
                    ),
                    {"id": batch_id},
                )
                .mappings()
                .first()
            )
        if row is None:
            raise LookupError(f"unknown batch: {batch_id!r}")
        cap = row["budget_cap_usd"]
        if cap is None:
            return
        total = row["total_cost_usd"] or 0
        if total >= cap:
            raise BudgetExceeded(f"batch {batch_id}: total {total} >= cap {cap}")
