"""DeadLetterStore — pipeline.dead_letter I/O.

Writes happen from StageRunner when a word's stage_runs.status='failed'
count hits 3 (spec §6 L470 "3 次都失败 → 写 dead_letter"). P7 scope is
single-run attempt tracking: a word can fail in one stage, retry on
`--force`, and reach attempt=3. Cross-run attempt counter is future work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import Engine


@dataclass
class DeadLetter:
    id: int
    word_id: int
    stage_name: str
    error: str
    attempt: int
    created_at: Any
    resolved_at: Any | None


@dataclass
class DeadLetterStore:
    engine: Engine

    def record(self, *, word_id: int, stage_name: str, error: str, attempt: int) -> int:
        """Insert a dead_letter row; returns the row id."""
        with self.engine.begin() as conn:
            return conn.execute(
                sa.text(
                    "INSERT INTO pipeline.dead_letter "
                    "(word_id, stage_name, error, attempt) "
                    "VALUES (:w, :s, :e, :a) RETURNING id"
                ),
                {"w": word_id, "s": stage_name, "e": error, "a": attempt},
            ).scalar_one()

    def list_open(self, *, limit: int = 100) -> list[DeadLetter]:
        """Unresolved failures."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT id, word_id, stage_name, error, attempt, "
                    "       created_at, resolved_at "
                    "FROM pipeline.dead_letter "
                    "WHERE resolved_at IS NULL "
                    "ORDER BY id DESC "
                    "LIMIT :lim"
                ),
                {"lim": limit},
            ).all()
        return [DeadLetter(*r) for r in rows]

    def replay(self, *, word_id: int) -> int:
        """Mark all open dead_letter rows for `word_id` as resolved and reset
        pipeline.words.status='new'. Returns number of dead_letter rows resolved.

        Raises LookupError if word_id does not exist in pipeline.words.
        """
        with self.engine.begin() as conn:
            exists = conn.execute(
                sa.text("SELECT 1 FROM pipeline.words WHERE id = :w"),
                {"w": word_id},
            ).scalar()
            if exists is None:
                raise LookupError(f"unknown word_id: {word_id}")
            resolved = (
                conn.execute(
                    sa.text(
                        "UPDATE pipeline.dead_letter SET resolved_at = now() "
                        "WHERE word_id = :w AND resolved_at IS NULL"
                    ),
                    {"w": word_id},
                ).rowcount
                or 0
            )
            # Reset pipeline.words so runner will re-try the full pipeline.
            # spec §6 L470: "dlq replay 重置 pipeline.words.status='new'"
            conn.execute(
                sa.text("UPDATE pipeline.words SET status = 'new' WHERE id = :w"),
                {"w": word_id},
            )
            return resolved
