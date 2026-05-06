"""add domain.words.status/quality_flag + meta schema (editors, sessions, audit)

Supports the web-admin editor workflow:
- domain.words gains status (0=draft,1=published,2=needs_review) and quality_flag
- meta.editors / meta.editor_sessions / meta.edit_audit for auth + audit trail

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-06 10:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. Extend domain.words with status + quality_flag ---
    op.execute(
        "ALTER TABLE domain.words "
        "ADD COLUMN status SMALLINT NOT NULL DEFAULT 0 "
        "CHECK (status IN (0, 1, 2))"
    )
    op.execute(
        "ALTER TABLE domain.words "
        "ADD COLUMN quality_flag TEXT NOT NULL DEFAULT 'none' "
        "CHECK (quality_flag IN ('none', 'suspect', 'fixed'))"
    )

    # --- 2. Backfill: words that have a serving payload are published ---
    op.execute(
        "UPDATE domain.words SET status = 1 "
        "WHERE word_id IN (SELECT word_id FROM serving.word_payload)"
    )

    # --- 3. Partial indexes on domain.words ---
    op.execute(
        "CREATE INDEX idx_domain_words_status "
        "ON domain.words (status) WHERE status IN (0, 2)"
    )
    op.execute(
        "CREATE INDEX idx_domain_words_quality "
        "ON domain.words (quality_flag) WHERE quality_flag <> 'none'"
    )

    # --- 4. Create meta schema ---
    op.execute("CREATE SCHEMA meta")

    # --- 5. meta.editors ---
    op.execute(
        "CREATE TABLE meta.editors ("
        "id BIGSERIAL PRIMARY KEY, "
        "email TEXT UNIQUE NOT NULL, "
        "password_hash TEXT NOT NULL, "
        "display_name TEXT NOT NULL, "
        "is_active BOOLEAN NOT NULL DEFAULT TRUE, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )

    # --- 6. meta.editor_sessions ---
    op.execute(
        "CREATE TABLE meta.editor_sessions ("
        "token_hash TEXT PRIMARY KEY, "
        "editor_id BIGINT NOT NULL REFERENCES meta.editors(id) ON DELETE CASCADE, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "expires_at TIMESTAMPTZ NOT NULL"
        ")"
    )
    op.execute(
        "CREATE INDEX idx_editor_sessions_editor "
        "ON meta.editor_sessions (editor_id)"
    )

    # --- 7. meta.edit_audit ---
    op.execute(
        "CREATE TABLE meta.edit_audit ("
        "id BIGSERIAL PRIMARY KEY, "
        "word_id BIGINT NOT NULL, "
        "field_path TEXT NOT NULL, "
        "target_id BIGINT, "
        "op TEXT NOT NULL CHECK (op IN ('update', 'insert', 'delete')), "
        "old_value JSONB, "
        "new_value JSONB, "
        "editor_id BIGINT NOT NULL REFERENCES meta.editors(id) ON DELETE RESTRICT, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    op.execute(
        "CREATE INDEX idx_edit_audit_word "
        "ON meta.edit_audit (word_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_edit_audit_editor "
        "ON meta.edit_audit (editor_id, created_at DESC)"
    )


def downgrade() -> None:
    # Reverse order: drop meta schema (CASCADE drops all tables + indexes),
    # then drop domain.words indexes and columns.
    op.execute("DROP SCHEMA meta CASCADE")

    op.execute("DROP INDEX IF EXISTS domain.idx_domain_words_quality")
    op.execute("DROP INDEX IF EXISTS domain.idx_domain_words_status")

    op.execute("ALTER TABLE domain.words DROP COLUMN quality_flag")
    op.execute("ALTER TABLE domain.words DROP COLUMN status")
