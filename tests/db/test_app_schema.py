"""app.* DDL smoke: all 5 tables exist after upgrade head.

Full constraint coverage (CHECK source, UNIQUE form+type, CASCADE, etc.)
lives in this file too — added in Task 5b."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from wordforge.db.engine import make_engine

WORDFORGE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _alembic(*args: str) -> None:
    subprocess.run(["uv", "run", "alembic", *args], cwd=WORDFORGE_DIR, check=True)


@pytest.fixture
def at_head() -> Iterator[sa.engine.Engine]:
    _alembic("downgrade", "base")
    _alembic("upgrade", "head")
    engine = make_engine()
    try:
        yield engine
    finally:
        engine.dispose()
        _alembic("downgrade", "base")


APP_TABLES = [
    "words",
    "meanings",
    "sentences",
    "mnemonics",
    "phrases",
]


def test_all_app_tables_exist(at_head):
    with at_head.connect() as conn:
        for t in APP_TABLES:
            found = conn.execute(
                sa.text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='domain' AND table_name=:t"
                ),
                {"t": t},
            ).scalar()
            assert found is not None, f"app.{t} missing"


def test_words_check_source_prefix_rejects_unknown(at_head):
    with at_head.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text("INSERT INTO domain.words (type, form, source) VALUES (1, 'hello', 'web:someone')")
        )


def test_words_unique_form_type_rejects_duplicate(at_head):
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (type, form, source) "
                "VALUES (1, 'apple', 'pipeline:claude-opus:paraphrase_v2')"
            )
        )
    with at_head.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (type, form, source) "
                "VALUES (1, 'apple', 'pipeline:claude-opus:paraphrase_v2')"
            )
        )


def test_words_same_form_different_type_allowed(at_head):
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (type, form, source) "
                "VALUES (1, 'set', 'pipeline:x:y'), (2, 'set', 'pipeline:x:y')"
            )
        )
        n = conn.execute(sa.text("SELECT count(*) FROM domain.words WHERE form='set'")).scalar()
        assert n == 2


def test_sentences_cascade_through_meanings(at_head):
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (word_id, type, form, source) "
                "VALUES (1, 1, 'bow', 'pipeline:x:y')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO domain.meanings (meaning_id, word_id, pos, source) "
                "VALUES (1, 1, 1, 'pipeline:x:y')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO domain.sentences (meaning_id, form, translation, source) "
                "VALUES (1, 'He took a bow.', '他鞠躬了', 'pipeline:x:y')"
            )
        )
    with at_head.begin() as conn:
        conn.execute(sa.text("DELETE FROM domain.words WHERE word_id = 1"))
    with at_head.connect() as conn:
        n = conn.execute(sa.text("SELECT count(*) FROM domain.sentences")).scalar()
        assert n == 0, "sentences should cascade through meanings when words is deleted"


def test_phrases_unique_owner_form(at_head):
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (word_id, type, form, source) "
                "VALUES (10, 1, 'pick', 'pipeline:x:y')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO domain.phrases (owner_word_id, form, meaning, source) "
                "VALUES (10, 'pick up', '拿起', 'pipeline:x:y')"
            )
        )
    with at_head.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO domain.phrases (owner_word_id, form, meaning, source) "
                "VALUES (10, 'pick up', '捡起', 'pipeline:x:y')"
            )
        )


def test_mnemonics_type_check_only_one(at_head):
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (word_id, type, form, source) "
                "VALUES (20, 1, 'ambiguous', 'pipeline:x:y')"
            )
        )
        with pytest.raises(IntegrityError):
            conn.execute(
                sa.text(
                    "INSERT INTO domain.mnemonics (word_id, type, content, source) "
                    "VALUES (20, 2, '{}'::jsonb, 'pipeline:x:y')"
                )
            )


def test_words_type_check_rejects_invalid(at_head):
    with at_head.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (type, form, source) VALUES (3, 'weird', 'pipeline:x:y')"
            )
        )


def test_mnemonics_cascade_on_word_delete(at_head):
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (word_id, type, form, source) "
                "VALUES (30, 1, 'alpha', 'pipeline:x:y')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO domain.mnemonics (word_id, type, content, source) "
                "VALUES (30, 1, '{}'::jsonb, 'pipeline:x:y')"
            )
        )
    with at_head.begin() as conn:
        conn.execute(sa.text("DELETE FROM domain.words WHERE word_id = 30"))
    with at_head.connect() as conn:
        n = conn.execute(sa.text("SELECT count(*) FROM domain.mnemonics WHERE word_id = 30")).scalar()
        assert n == 0, "mnemonics should cascade when its word is deleted"


def test_phrases_cascade_on_word_delete(at_head):
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (word_id, type, form, source) "
                "VALUES (40, 1, 'beta', 'pipeline:x:y')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO domain.phrases (owner_word_id, form, meaning, source) "
                "VALUES (40, 'beta test', '测试', 'pipeline:x:y')"
            )
        )
    with at_head.begin() as conn:
        conn.execute(sa.text("DELETE FROM domain.words WHERE word_id = 40"))
    with at_head.connect() as conn:
        n = conn.execute(
            sa.text("SELECT count(*) FROM domain.phrases WHERE owner_word_id = 40")
        ).scalar()
        assert n == 0, "phrases should cascade when its owner word is deleted"


@pytest.mark.parametrize(
    "child_insert_sql",
    [
        # meanings: needs a parent word first, then violate the meanings.source CHECK
        "INSERT INTO domain.meanings (word_id, source) VALUES (50, 'web:someone')",
        # sentences: needs word+meaning, then violate sentences.source CHECK
        "INSERT INTO domain.sentences (meaning_id, form, translation, source) "
        "VALUES (50, 'x', 'x', 'web:someone')",
        # mnemonics: needs parent word, then violate mnemonics.source CHECK
        "INSERT INTO domain.mnemonics (word_id, type, content, source) "
        "VALUES (50, 1, '{}'::jsonb, 'web:someone')",
        # phrases: needs parent word, then violate phrases.source CHECK
        "INSERT INTO domain.phrases (owner_word_id, form, meaning, source) "
        "VALUES (50, 'x y', 'x', 'web:someone')",
    ],
    ids=["meanings", "sentences", "mnemonics", "phrases"],
)
def test_child_tables_check_source_prefix_rejects_unknown(at_head, child_insert_sql):
    # Seed parent word + a meaning (so both word_id=50 and meaning_id=50 are valid FKs)
    with at_head.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (word_id, type, form, source) "
                "VALUES (50, 1, 'gamma', 'pipeline:x:y')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO domain.meanings (meaning_id, word_id, source) "
                "VALUES (50, 50, 'pipeline:x:y')"
            )
        )
    with at_head.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(sa.text(child_insert_sql))
