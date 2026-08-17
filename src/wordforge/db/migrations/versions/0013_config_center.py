"""web config center (M9): versioned provider configs, prompts, agents

Operator-facing LLM configuration for the web admin. Three versioned
entities live in meta.* alongside the other web operational tables:

- provider_configs: named endpoint + transport; the sk key is entered by
  the operator, stored encrypted (Fernet, key from WORDFORGE_CONFIG_SECRET
  env) on the PARENT row only — versions record non-secret fields only.
- prompts: versioned template content with {word}/{dict_summary} slots.
- agents: versioned composition pinning (provider config version + model
  + prompt version + params). Rollback = moving the parent's
  current_version_id pointer (history stays append-only).

current_version_id columns are plain BIGINT (no FK) to avoid circular
FKs with the *_versions tables; integrity is enforced by the service.

experiment_runs gains agent_version_id + resolved_snapshot so every run
records exactly which component versions produced it (audit).

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_configs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        # parent-level secret state (not versioned)
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_key_last4", sa.Text(), nullable=True),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("meta.editors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="meta",
    )

    op.create_table(
        "provider_config_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "config_id",
            sa.BigInteger(),
            sa.ForeignKey("meta.provider_configs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "transport IN ('openai', 'anthropic')", name="ck_pcv_transport"
        ),
        sa.Column("transport", sa.Text(), nullable=False, server_default="openai"),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("meta.editors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("config_id", "version", name="uq_pcv_config_version"),
        schema="meta",
    )

    op.create_table(
        "prompts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("meta.editors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="meta",
    )

    op.create_table(
        "prompt_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "prompt_id",
            sa.BigInteger(),
            sa.ForeignKey("meta.prompts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("meta.editors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("prompt_id", "version", name="uq_pv_prompt_version"),
        schema="meta",
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("current_version_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("meta.editors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        schema="meta",
    )

    op.create_table(
        "agent_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "agent_id",
            sa.BigInteger(),
            sa.ForeignKey("meta.agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "provider_config_id",
            sa.BigInteger(),
            sa.ForeignKey("meta.provider_configs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider_config_version", sa.Integer(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column(
            "prompt_id",
            sa.BigInteger(),
            sa.ForeignKey("meta.prompts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by",
            sa.BigInteger(),
            sa.ForeignKey("meta.editors.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("agent_id", "version", name="uq_av_agent_version"),
        schema="meta",
    )

    with op.batch_alter_table("experiment_runs", schema="meta") as batch:
        batch.add_column(sa.Column("agent_version_id", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("resolved_snapshot", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("experiment_runs", schema="meta") as batch:
        batch.drop_column("resolved_snapshot")
        batch.drop_column("agent_version_id")
    op.drop_table("agent_versions", schema="meta")
    op.drop_table("agents", schema="meta")
    op.drop_table("prompt_versions", schema="meta")
    op.drop_table("prompts", schema="meta")
    op.drop_table("provider_config_versions", schema="meta")
    op.drop_table("provider_configs", schema="meta")
