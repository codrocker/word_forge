"""drop pipeline.words.source_word_id; start app.words.word_id sequence at 100001

Background: 0004 added pipeline.words.source_word_id so recover_from_momo could
carry the upstream momo word_id and export would INSERT it as the explicit
app.words.word_id. In practice the vocabulary will never exceed ~1M rows, so
bumping the BIGSERIAL start to 100001 gives plenty of headroom and keeps
id ordering aligned with ingest order (dump ORDER BY word_id → ingest in that
order → serial assigns in that order). Drops the explicit-id plumbing entirely.

Idempotent: empty tables at this point for any env that relies on post-recovery
data, but even when app.words already has rows, setrval picks MAX(word_id)+1
or the target, whichever is larger, so existing IDs stay stable.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-01 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE pipeline.words DROP COLUMN IF EXISTS source_word_id")
    # BIGSERIAL sequence name follows PG convention: <table>_<col>_seq.
    # setval(..., 100001, false) means next nextval() returns exactly 100001;
    # GREATEST(...) keeps any existing larger value so we never rewind and
    # collide with rows already in app.words.
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('app.words', 'word_id'),
            GREATEST(
                100001,
                COALESCE((SELECT MAX(word_id) + 1 FROM app.words), 100001)
            ),
            false
        )
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE pipeline.words ADD COLUMN source_word_id BIGINT NULL")
    # Sequence is left where it is; rewinding would risk colliding with
    # existing rows. Caller can manually setval if truly needed.
