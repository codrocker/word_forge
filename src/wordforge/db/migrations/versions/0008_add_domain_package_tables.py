"""add domain.package, domain.package_unit, domain.package_word tables

Background: wordforge mirrors the vocabulary packages that originate in
momo MySQL (tables `package_new`, `package_unit`, `package_word`). The
backend service will read these via serving.word_payload (future PR),
but wordforge owns the canonical copy inside `domain.*` because:
  - package metadata is static lexicon data (not user state)
  - it's part of the same source-of-truth chain as domain.words /
    domain.meanings, so it belongs next to them
  - user-state tables (package_memorized_*, word_record_*) live in
    the backend DB — not here

Design decisions:
- Mirror `package_new` (not legacy `package`) — it's what production
  reads. `package_new`'s `score` field is kept; legacy `creator_id`
  is dropped.
- Legacy SQL reserved word `order` → `sort_order` (PG choke point).
- No FK on `package_word.word_id -> domain.words.word_id` despite the
  data being consistent: mirror runs periodically, and wordforge may
  have a word the mirror hasn't picked up yet (or vice versa). Enforce
  via mirror script invariant checks, not DDL — easier to diagnose.
- FKs between package tables (package_unit.package_id -> package.id,
  package_word.{package_id, unit_id} -> parents) ARE enforced: those
  come from a single MySQL source in one mirror pass, so consistency
  is guaranteed per-run.
- Revisions: none. Mirror is idempotent full refresh (TRUNCATE +
  COPY). No history table here; if we need revision tracking later
  it lives in serving.*.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-02 01:40:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE domain.package (
            package_id     BIGINT PRIMARY KEY,
            type           SMALLINT NOT NULL,
            category_code  INTEGER NOT NULL,
            title          TEXT NOT NULL,
            word_count     INTEGER NOT NULL,
            intro          TEXT,
            author         TEXT,
            isbn           TEXT,
            publisher      TEXT,
            org            TEXT,
            version        TEXT,
            score          INTEGER NOT NULL,
            status         SMALLINT NOT NULL,
            created_at_ms  BIGINT NOT NULL,
            updated_at_ms  BIGINT NOT NULL
        )
        """
    )
    op.execute("CREATE INDEX idx_domain_package_category ON domain.package (category_code)")
    op.execute("CREATE INDEX idx_domain_package_status   ON domain.package (status)")

    op.execute(
        """
        CREATE TABLE domain.package_unit (
            unit_id     BIGINT PRIMARY KEY,
            package_id  BIGINT NOT NULL REFERENCES domain.package(package_id) ON DELETE CASCADE,
            title       TEXT NOT NULL,
            sort_order  DOUBLE PRECISION NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_domain_package_unit_package ON domain.package_unit (package_id)")

    op.execute(
        """
        CREATE TABLE domain.package_word (
            p_word_id   BIGINT PRIMARY KEY,
            package_id  BIGINT NOT NULL REFERENCES domain.package(package_id) ON DELETE CASCADE,
            unit_id     BIGINT NOT NULL REFERENCES domain.package_unit(unit_id) ON DELETE CASCADE,
            word_id     BIGINT NOT NULL,
            sort_order  DOUBLE PRECISION NOT NULL,
            importance  BIGINT,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX idx_domain_package_word_package ON domain.package_word (package_id)")
    op.execute("CREATE INDEX idx_domain_package_word_unit    ON domain.package_word (unit_id)")
    op.execute("CREATE INDEX idx_domain_package_word_word    ON domain.package_word (word_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS domain.package_word")
    op.execute("DROP TABLE IF EXISTS domain.package_unit")
    op.execute("DROP TABLE IF EXISTS domain.package")
