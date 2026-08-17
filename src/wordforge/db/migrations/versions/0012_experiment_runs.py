"""add meta.experiment_runs for web LLM experiments (M8)

One row per experiment run: a chosen (provider, model, stage, prompt
override) applied to a seeded sample of words, with per-word results and
costs stored as JSONB for side-by-side comparison in the web admin.

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "experiment_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "editor_id",
            sa.BigInteger(),
            sa.ForeignKey("meta.editors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("prompt_override", sa.Text(), nullable=True),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("word_ids", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="running"),
        sa.CheckConstraint(
            "status IN ('running', 'done', 'error')", name="ck_experiment_runs_status"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("results", postgresql.JSONB(), nullable=True),
        sa.Column(
            "total_cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"
        ),
        sa.Column("ok_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        schema="meta",
    )
    op.create_index(
        "idx_experiment_runs_created",
        "experiment_runs",
        ["created_at"],
        schema="meta",
    )


def downgrade() -> None:
    op.drop_index(
        "idx_experiment_runs_created", table_name="experiment_runs", schema="meta"
    )
    op.drop_table("experiment_runs", schema="meta")
