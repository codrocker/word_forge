"""create app.* tables (words / meanings / sentences / mnemonics / phrases)

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-29 00:00:01
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE app.words (
          word_id             BIGSERIAL PRIMARY KEY,
          type                SMALLINT NOT NULL CHECK (type IN (1,2)),
          form                TEXT NOT NULL,
          phonetic_us         TEXT,
          phonetic_uk         TEXT,
          audio_us            TEXT,
          audio_uk            TEXT,
          structure           JSONB,
          plural              TEXT,
          past_tense          TEXT,
          past_participle     TEXT,
          third_person        TEXT,
          present_participle  TEXT,
          comparative         TEXT,
          superlative         TEXT,
          derivatives         JSONB,
          source              TEXT NOT NULL,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (form, type),
          CHECK (source LIKE 'pipeline:%'
              OR source LIKE 'human:%'
              OR source LIKE 'import:%')
        )
    """)

    op.execute("""
        CREATE TABLE app.meanings (
          meaning_id    BIGSERIAL PRIMARY KEY,
          word_id       BIGINT NOT NULL REFERENCES app.words(word_id) ON DELETE CASCADE,
          pos           SMALLINT,
          pos_sub       SMALLINT,
          cn_paraphrase TEXT,
          en_paraphrase TEXT,
          equivalents   JSONB,
          synonyms      JSONB,
          antonyms      JSONB,
          source        TEXT NOT NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (source LIKE 'pipeline:%'
              OR source LIKE 'human:%'
              OR source LIKE 'import:%')
        )
    """)
    op.execute("CREATE INDEX ON app.meanings(word_id)")

    op.execute("""
        CREATE TABLE app.sentences (
          sentence_id     BIGSERIAL PRIMARY KEY,
          meaning_id      BIGINT NOT NULL REFERENCES app.meanings(meaning_id) ON DELETE CASCADE,
          form            TEXT NOT NULL,
          translation     TEXT NOT NULL,
          highlight       JSONB,
          citation        SMALLINT,
          citation_detail JSONB,
          source          TEXT NOT NULL,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (source LIKE 'pipeline:%'
              OR source LIKE 'human:%'
              OR source LIKE 'import:%')
        )
    """)
    op.execute("CREATE INDEX ON app.sentences(meaning_id)")

    op.execute("""
        CREATE TABLE app.mnemonics (
          mnemonic_id BIGSERIAL PRIMARY KEY,
          word_id     BIGINT NOT NULL REFERENCES app.words(word_id) ON DELETE CASCADE,
          type        SMALLINT NOT NULL CHECK (type = 1),
          content     JSONB NOT NULL,
          source      TEXT NOT NULL,
          created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK (source LIKE 'pipeline:%'
              OR source LIKE 'human:%'
              OR source LIKE 'import:%')
        )
    """)
    op.execute("CREATE INDEX ON app.mnemonics(word_id)")

    op.execute("""
        CREATE TABLE app.phrases (
          phrase_id     BIGSERIAL PRIMARY KEY,
          owner_word_id BIGINT NOT NULL REFERENCES app.words(word_id) ON DELETE CASCADE,
          form          TEXT NOT NULL,
          meaning       TEXT NOT NULL,
          source        TEXT NOT NULL,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (owner_word_id, form),
          CHECK (source LIKE 'pipeline:%'
              OR source LIKE 'human:%'
              OR source LIKE 'import:%')
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app.phrases")
    op.execute("DROP TABLE IF EXISTS app.mnemonics")
    op.execute("DROP TABLE IF EXISTS app.sentences")
    op.execute("DROP TABLE IF EXISTS app.meanings")
    op.execute("DROP TABLE IF EXISTS app.words")
