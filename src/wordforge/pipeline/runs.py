"""StageRunStore — pipeline.stage_runs I/O + atomic cost accumulation.

Each successful run: INSERT stage_runs row + UPDATE batches.total_cost_usd in
the SAME transaction. Atomic record-keeping: if the bump fails, the row
doesn't appear either.

Not here: retry policy, DLQ entry, dead_letter writes. A stage calling
record_failed 3 times is P5's problem (P5 wires tenacity + DLQ).
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Engine


@dataclass
class StageRunStore:
    engine: Engine

    def record_ok(
        self,
        *,
        batch_id: str | None,
        word_id: int,
        stage_name: str,
        model: str | None,
        tokens_in: int | None,
        tokens_out: int | None,
        cost_usd: float,
        duration_ms: int | None,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO pipeline.stage_runs "
                    "(batch_id, word_id, stage_name, status, model, "
                    " tokens_input, tokens_output, cost_usd, duration_ms, "
                    " finished_at) "
                    "VALUES (:batch, :w, :s, 'ok', :model, :ti, :to, :cost, "
                    "        :dur, now())"
                ),
                {
                    "batch": batch_id,
                    "w": word_id,
                    "s": stage_name,
                    "model": model,
                    "ti": tokens_in,
                    "to": tokens_out,
                    "cost": cost_usd,
                    "dur": duration_ms,
                },
            )
            if batch_id is not None:
                # Round 3 R3-codex-1: migration 0003 declares total_cost_usd
                # NUMERIC(12,6) DEFAULT 0 WITHOUT NOT NULL. A manually inserted
                # row with total_cost_usd=NULL would make `total + :cost`
                # propagate NULL (PG SQL semantics). COALESCE keeps accumulation
                # correct for NULL-seeded batches; future migrations may add
                # NOT NULL but P3 must not depend on it.
                upd = conn.execute(
                    sa.text(
                        "UPDATE pipeline.batches "
                        "SET total_cost_usd = COALESCE(total_cost_usd, 0) + :cost "
                        "WHERE id = :id"
                    ),
                    {"cost": cost_usd, "id": batch_id},
                )
                # Round 1 U-codex-1: never silently miscount cost. If
                # batch_id is a typo (row doesn't exist), rowcount == 0 →
                # raise inside the same txn so the stage_runs row is rolled
                # back too. Not a new mechanism, one-line fail-loud guard.
                if upd.rowcount != 1:
                    raise LookupError(
                        f"StageRunStore.record_ok: batch {batch_id!r} not "
                        f"found; stage_runs + cost UPDATE rolled back"
                    )

    # `record_skipped` is intentionally absent — see Task 2 scope note
    # (Round 2 R2-D2). P5 Export stage writes status='skipped' inline in
    # its own export transaction, not via this helper.

    def failed_attempt_count(self, *, word_id: int, stage_name: str, batch_id: str | None) -> int:
        """Count stage_runs.status='failed' for (word_id, stage_name) since the
        last dead_letter.resolved_at (inclusive) for the same pair. This way a
        `dlq replay` effectively resets the attempt counter -- the pre-replay
        failures no longer count toward the next DLQ threshold."""
        sql = (
            "SELECT count(*) FROM pipeline.stage_runs sr "
            "WHERE sr.word_id = :w AND sr.stage_name = :s AND sr.status = 'failed' "
            "AND sr.started_at > COALESCE("
            "  (SELECT MAX(resolved_at) FROM pipeline.dead_letter "
            "   WHERE word_id = :w AND stage_name = :s AND resolved_at IS NOT NULL),"
            "  TIMESTAMP '1970-01-01'"
            ")"
        )
        params: dict[str, object] = {"w": word_id, "s": stage_name}
        if batch_id is not None:
            sql += " AND sr.batch_id = :b"
            params["b"] = batch_id
        with self.engine.connect() as conn:
            return int(conn.execute(sa.text(sql), params).scalar_one())

    def record_failed(
        self,
        *,
        batch_id: str | None,
        word_id: int,
        stage_name: str,
        error: str,
        model: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO pipeline.stage_runs "
                    "(batch_id, word_id, stage_name, status, model, "
                    " duration_ms, error, finished_at) "
                    "VALUES (:batch, :w, :s, 'failed', :model, :dur, :err, now())"
                ),
                {
                    "batch": batch_id,
                    "w": word_id,
                    "s": stage_name,
                    "model": model,
                    "dur": duration_ms,
                    "err": error,
                },
            )
