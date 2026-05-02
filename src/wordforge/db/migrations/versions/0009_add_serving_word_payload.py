"""add serving.word_payload: aggregated JSONB read model for the backend

Background: backend fetches one word's entire renderable data by word_id.
Canonical relational tables (domain.*) are normalized for pipeline
integrity but require 5 JOINs to reconstruct a word for display.
serving.word_payload is a denormalized read model updated atomically
with domain.* inside the export stage's transaction — one SELECT by
word_id returns everything needed to render the card.

Minimal initial schema per codex review: only contract fields now,
no versioning/revision infrastructure. We can add payload_schema_version
bump, source_fingerprint, is_published, etc. the first time we actually
need them (per the P1 note in the earlier review). YAGNI wins.

Payload structure (schema_version 1):
  {
    "form": "apple",
    "type": 1,
    "phonetic": {"us": "...", "uk": "...", "audio_us": "...", "audio_uk": "..."},
    "meanings": [{"meaning_id": N, "pos": N, "cn": "...", "en": "...",
                  "user_group": N, ...}, ...],
    "sentences": [{"sentence_id": N, "meaning_id": N, "en": "...",
                   "cn": "...", ...}, ...],
    "mnemonic": {"content": {...}, "type": N},
    "phrases": [...],
    "packages": [{"package_id": N, "unit_id": N, "sort_order": N,
                  "importance": N}, ...]
  }

The `packages` array makes this self-contained — the backend doesn't
need to JOIN domain.package_word. Backend caches by word_id; if user
toggles package subscription that's a separate user-state read.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-02 02:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS serving")
    op.execute(
        """
        CREATE TABLE serving.word_payload (
            word_id                BIGINT PRIMARY KEY,
            form                   TEXT NOT NULL,
            type                   SMALLINT NOT NULL,
            payload                JSONB NOT NULL,
            payload_schema_version SMALLINT NOT NULL DEFAULT 1,
            updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    # form lookup (lowercase-prefix search for backend autocomplete)
    op.execute("CREATE INDEX idx_serving_word_payload_form ON serving.word_payload (form)")
    # Staleness scan (recently updated first)
    op.execute(
        "CREATE INDEX idx_serving_word_payload_updated "
        "ON serving.word_payload (updated_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS serving.word_payload")
    # Keep schema "serving" around — harmless, saves re-creation on future migrations.
