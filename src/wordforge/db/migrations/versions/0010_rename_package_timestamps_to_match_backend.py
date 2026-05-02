"""rename domain.package.created_at_ms/updated_at_ms -> created_at/updated_at

Background: the `_ms` suffix is wrong — momo's `package_new.create_at /
update_at` are BIGINT epoch SECONDS (10-digit values like 1732779024),
not milliseconds. 0008 picked up the misleading name and made it worse
by tacking on `_ms`.

Backend contract (Feishu wiki BGXgwKITAiWOrAki3S8c0dBknqh) defines the
columns as `created_at / updated_at` BIGINT (seconds). Rename only; the
values and BIGINT type stay exactly as they are.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-02 11:15:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE domain.package RENAME COLUMN created_at_ms TO created_at")
    op.execute("ALTER TABLE domain.package RENAME COLUMN updated_at_ms TO updated_at")


def downgrade() -> None:
    op.execute("ALTER TABLE domain.package RENAME COLUMN updated_at TO updated_at_ms")
    op.execute("ALTER TABLE domain.package RENAME COLUMN created_at TO created_at_ms")
