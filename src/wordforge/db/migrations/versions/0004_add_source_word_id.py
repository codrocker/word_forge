"""add source_word_id slot to pipeline.words

pipeline.words needs a field to carry the upstream canonical id (e.g. momo
MySQL word.word_id) from ingest → export. export will use this value as
the explicit app.words.word_id (bypassing the serial) so external IDs
round-trip. app.words itself does NOT get a new column — the bigint
word_id is the single id surface; rows without upstream origin use the
serial default.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-30 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE pipeline.words ADD COLUMN source_word_id BIGINT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline.words DROP COLUMN IF EXISTS source_word_id")
