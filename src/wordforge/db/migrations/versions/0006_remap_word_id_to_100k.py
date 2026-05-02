"""remap app.words.word_id from mysql billions to compact 10^5 range

Background: momo MySQL stores word_id in the 1,000,000,001 range (122k words).
We've been carrying those values through ingest and export, so app.words.word_id
ended up at 10**9. Going forward we want a compact 10**5 ID range starting at
100001 — cleaner for humans reading logs and URLs, and every bigint column
is still bigint so no performance delta.

Mapping: new_id = old_id - 999_900_000
  - 1_000_000_001 → 100_001
  - 1_000_122_664 → 222_664

This migration shifts every column that carries `app.words.word_id` by the
same constant. `pipeline.words.id` is an internal BIGSERIAL that has nothing
to do with app_word_id, so stage_artifacts/stage_runs/dead_letter that FK
into pipeline.words.id are NOT touched.

Idempotent safety:
- The WHERE filter (word_id > 999_000_000) skips rows already remapped, so
  re-running the migration on already-remapped data is a no-op.
- Constraints run inside a deferrable transaction so FK parent/child can be
  updated together.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-02 00:30:00
"""

from __future__ import annotations

# Long SQL lines with aligned column names are easier to audit side-by-side
# than auto-wrapped ones; disable E501 for this migration only.
# ruff: noqa: E501
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_SHIFT = 999_900_000


def upgrade() -> None:
    # FKs in 0002 were NOT declared DEFERRABLE, so `SET CONSTRAINTS ALL DEFERRED`
    # is a no-op for them. Drop the three FKs pointing at app.words.word_id,
    # shift every column in one transaction, then re-add the FKs with the same
    # semantics (immediate, non-deferrable) afterwards. app.sentences.meaning_id
    # FK is untouched because meaning_id is not remapped.
    op.execute("ALTER TABLE app.meanings  DROP CONSTRAINT meanings_word_id_fkey")
    op.execute("ALTER TABLE app.mnemonics DROP CONSTRAINT mnemonics_word_id_fkey")
    op.execute("ALTER TABLE app.phrases   DROP CONSTRAINT phrases_owner_word_id_fkey")

    # app.* + pipeline.words layer. Guard `> 999_000_000` makes the UPDATE
    # a no-op on already-remapped data (re-runs, test DBs that never had
    # 10^9 values). pipeline.words.app_word_id has NO FK by DDL (see 0003),
    # but must stay in lockstep with app.words.word_id — same shift.
    for sql in (
        "UPDATE app.words      SET word_id       = word_id       - :s WHERE word_id       > 999000000",
        "UPDATE app.meanings   SET word_id       = word_id       - :s WHERE word_id       > 999000000",
        "UPDATE app.mnemonics  SET word_id       = word_id       - :s WHERE word_id       > 999000000",
        "UPDATE app.phrases    SET owner_word_id = owner_word_id - :s WHERE owner_word_id > 999000000",
        "UPDATE pipeline.words SET app_word_id   = app_word_id   - :s WHERE app_word_id   > 999000000",
    ):
        op.execute(sa.text(sql).bindparams(s=_SHIFT))

    # Re-add FKs. ON DELETE CASCADE is load-bearing — the original 0002 DDL
    # has it on every child FK and several tests rely on it. Dropping CASCADE
    # would silently change the semantics of `DELETE FROM app.words`.
    op.execute(
        "ALTER TABLE app.meanings  ADD CONSTRAINT meanings_word_id_fkey "
        "FOREIGN KEY (word_id) REFERENCES app.words(word_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE app.mnemonics ADD CONSTRAINT mnemonics_word_id_fkey "
        "FOREIGN KEY (word_id) REFERENCES app.words(word_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE app.phrases   ADD CONSTRAINT phrases_owner_word_id_fkey "
        "FOREIGN KEY (owner_word_id) REFERENCES app.words(word_id) ON DELETE CASCADE"
    )

    # Reset the BIGSERIAL so nextval gives 222665 (max current + 1) and
    # future ingests slot into the compact range rather than jumping back to
    # whatever the sequence was at (10^9 territory after 0005).
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('app.words', 'word_id'),
            GREATEST(
                222665,
                COALESCE((SELECT MAX(word_id) + 1 FROM app.words), 222665)
            ),
            false
        )
        """
    )


def downgrade() -> None:
    # Symmetric: drop FKs, add back the shift, re-create FKs.
    op.execute("ALTER TABLE app.meanings  DROP CONSTRAINT meanings_word_id_fkey")
    op.execute("ALTER TABLE app.mnemonics DROP CONSTRAINT mnemonics_word_id_fkey")
    op.execute("ALTER TABLE app.phrases   DROP CONSTRAINT phrases_owner_word_id_fkey")
    for sql in (
        "UPDATE app.words      SET word_id       = word_id       + :s WHERE word_id       < 999000000",
        "UPDATE app.meanings   SET word_id       = word_id       + :s WHERE word_id       < 999000000",
        "UPDATE app.mnemonics  SET word_id       = word_id       + :s WHERE word_id       < 999000000",
        "UPDATE app.phrases    SET owner_word_id = owner_word_id + :s WHERE owner_word_id < 999000000",
        "UPDATE pipeline.words SET app_word_id   = app_word_id   + :s WHERE app_word_id   < 999000000",
    ):
        op.execute(sa.text(sql).bindparams(s=_SHIFT))
    op.execute(
        "ALTER TABLE app.meanings  ADD CONSTRAINT meanings_word_id_fkey "
        "FOREIGN KEY (word_id) REFERENCES app.words(word_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE app.mnemonics ADD CONSTRAINT mnemonics_word_id_fkey "
        "FOREIGN KEY (word_id) REFERENCES app.words(word_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE app.phrases   ADD CONSTRAINT phrases_owner_word_id_fkey "
        "FOREIGN KEY (owner_word_id) REFERENCES app.words(word_id) ON DELETE CASCADE"
    )
    op.execute(
        """
        SELECT setval(
            pg_get_serial_sequence('app.words', 'word_id'),
            COALESCE((SELECT MAX(word_id) + 1 FROM app.words), 1000000001),
            false
        )
        """
    )
