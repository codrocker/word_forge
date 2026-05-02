"""create pipeline.* tables (batches / words / stage_artifacts / stage_runs
/ external_call_cache / dead_letter)

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-29 00:00:02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE pipeline.batches (
          id              TEXT PRIMARY KEY,
          label           TEXT,
          total_cost_usd  NUMERIC(12,6) DEFAULT 0,
          budget_cap_usd  NUMERIC(12,6),
          status          TEXT NOT NULL DEFAULT 'running'
            CHECK (status IN ('running','done','aborted')),
          started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          finished_at     TIMESTAMPTZ
        )
    """)

    op.execute("""
        CREATE TABLE pipeline.words (
          id              BIGSERIAL PRIMARY KEY,
          raw_form        TEXT NOT NULL,
          normalized_form TEXT NOT NULL,
          type            SMALLINT NOT NULL CHECK (type IN (1,2)),
          app_word_id     BIGINT,
          status          TEXT NOT NULL DEFAULT 'new'
            CHECK (status IN ('new','in_progress','done','failed')),
          batch_id        TEXT REFERENCES pipeline.batches(id),
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (normalized_form, type)
        )
    """)

    op.execute("""
        CREATE TABLE pipeline.stage_artifacts (
          word_id        BIGINT NOT NULL REFERENCES pipeline.words(id) ON DELETE CASCADE,
          stage_name     TEXT NOT NULL,
          fingerprint    TEXT NOT NULL,
          payload        JSONB NOT NULL,
          source         TEXT NOT NULL,
          model          TEXT,
          prompt_version TEXT,
          updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (word_id, stage_name)
        )
    """)

    op.execute("""
        CREATE TABLE pipeline.stage_runs (
          id           BIGSERIAL PRIMARY KEY,
          batch_id     TEXT,
          word_id      BIGINT NOT NULL,
          stage_name   TEXT NOT NULL,
          status       TEXT NOT NULL CHECK (status IN ('ok','failed','skipped')),
          model        TEXT,
          tokens_input INT,
          tokens_output INT,
          cost_usd     NUMERIC(10,6),
          duration_ms  INT,
          error        TEXT,
          started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
          finished_at  TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX ON pipeline.stage_runs(word_id, stage_name)")
    op.execute("CREATE INDEX ON pipeline.stage_runs(batch_id)")

    op.execute("""
        CREATE TABLE pipeline.external_call_cache (
          cache_key  TEXT PRIMARY KEY,
          kind       TEXT NOT NULL,
          response   JSONB NOT NULL,
          cost_usd   NUMERIC(10,6),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE pipeline.dead_letter (
          id          BIGSERIAL PRIMARY KEY,
          word_id     BIGINT NOT NULL,
          stage_name  TEXT NOT NULL,
          error       TEXT NOT NULL,
          attempt     INT NOT NULL,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          resolved_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX ON pipeline.dead_letter(word_id)")
    op.execute("CREATE INDEX ON pipeline.dead_letter(resolved_at) WHERE resolved_at IS NULL")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS pipeline.dead_letter")
    op.execute("DROP TABLE IF EXISTS pipeline.external_call_cache")
    op.execute("DROP TABLE IF EXISTS pipeline.stage_runs")
    op.execute("DROP TABLE IF EXISTS pipeline.stage_artifacts")
    op.execute("DROP TABLE IF EXISTS pipeline.words")
    op.execute("DROP TABLE IF EXISTS pipeline.batches")
