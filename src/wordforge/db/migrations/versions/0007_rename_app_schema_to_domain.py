"""rename schema 'app' to 'domain'

Background: two independent reviews (codex + gemini) pushed back on
`app.*` as the schema name for canonical relational data. Learners
reading code see `app` and think "application layer = what the backend
consumes", but the data backend actually consumes will live in
`serving.*`. `app` is really the domain model (words / meanings /
sentences / mnemonics / phrases as business entities), so renaming
to `domain` removes the long-term ambiguity before more code is
written against the old name.

PostgreSQL `ALTER SCHEMA ... RENAME TO ...` is atomic and transactional
— all tables, indexes, constraints, and sequences inside move together,
so no FK rewrites are needed. `pg_get_serial_sequence('app.words',
'word_id')` references are resolved at call time by 0006's UPDATEs
(which already ran), so renaming afterwards is safe.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-02 01:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER SCHEMA app RENAME TO domain")


def downgrade() -> None:
    op.execute("ALTER SCHEMA domain RENAME TO app")
