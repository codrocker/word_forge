"""create app and pipeline schemas

Revision ID: 0001
Revises:
Create Date: 2026-04-29 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("CREATE SCHEMA IF NOT EXISTS pipeline")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS pipeline CASCADE")
    op.execute("DROP SCHEMA IF EXISTS app CASCADE")
