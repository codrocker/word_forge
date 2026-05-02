"""pipeline.* DDL smoke: all 6 tables exist after upgrade 0003.

Full constraint coverage (status CHECK, UNIQUE(normalized_form,type),
FK->batches, stage_artifacts PK, stage_runs no-FK audit,
dead_letter partial index) added in Task 6b."""

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
def at_0003() -> Iterator[sa.engine.Engine]:
    _alembic("downgrade", "base")
    _alembic("upgrade", "0003")
    engine = make_engine()
    try:
        yield engine
    finally:
        engine.dispose()
        _alembic("downgrade", "base")


PIPELINE_TABLES = [
    "batches",
    "words",
    "stage_artifacts",
    "stage_runs",
    "external_call_cache",
    "dead_letter",
]


def test_all_pipeline_tables_exist(at_0003):
    with at_0003.connect() as conn:
        for t in PIPELINE_TABLES:
            found = conn.execute(
                sa.text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='pipeline' AND table_name=:t"
                ),
                {"t": t},
            ).scalar()
            assert found is not None, f"pipeline.{t} missing"


def test_batches_status_check(at_0003):
    with at_0003.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(sa.text("INSERT INTO pipeline.batches (id, status) VALUES ('B1', 'zombie')"))


def test_words_unique_normalized_form_type(at_0003):
    with at_0003.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.words (raw_form, normalized_form, type) "
                "VALUES ('Apples', 'apple', 1)"
            )
        )
    with at_0003.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.words (raw_form, normalized_form, type) "
                "VALUES ('APPLE', 'apple', 1)"
            )
        )


def test_words_fk_to_batches(at_0003):
    with at_0003.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.words "
                "(raw_form, normalized_form, type, batch_id) "
                "VALUES ('x', 'x', 1, 'does-not-exist')"
            )
        )


def test_words_status_check(at_0003):
    with at_0003.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.words "
                "(raw_form, normalized_form, type, status) "
                "VALUES ('x', 'x', 1, 'weird')"
            )
        )


def test_stage_artifacts_pk_word_id_stage_name(at_0003):
    with at_0003.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.words "
                "(id, raw_form, normalized_form, type) VALUES (1, 'x', 'x', 1)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.stage_artifacts "
                "(word_id, stage_name, fingerprint, payload, source) "
                "VALUES (1, 'fetch_dict', 'fp1', '{}'::jsonb, 'pipeline:x')"
            )
        )
    with at_0003.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.stage_artifacts "
                "(word_id, stage_name, fingerprint, payload, source) "
                "VALUES (1, 'fetch_dict', 'fp2', '{}'::jsonb, 'pipeline:x')"
            )
        )


def test_stage_runs_status_check(at_0003):
    with at_0003.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.stage_runs "
                "(word_id, stage_name, status) VALUES (1, 'fetch_dict', 'derp')"
            )
        )


def test_stage_runs_word_id_has_no_fk(at_0003):
    """Audit retention: stage_runs.word_id logically points to pipeline.words(id)
    but has NO FK, so runs survive word deletion."""
    with at_0003.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.stage_runs "
                "(word_id, stage_name, status) VALUES (999999, 'fetch_dict', 'ok')"
            )
        )
        n = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.stage_runs WHERE word_id = 999999")
        ).scalar()
        assert n == 1


def test_dead_letter_partial_index_on_unresolved(at_0003):
    with at_0003.connect() as conn:
        got = conn.execute(
            sa.text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname='pipeline' AND tablename='dead_letter' "
                "AND indexdef ILIKE '%WHERE (resolved_at IS NULL)%'"
            )
        ).scalar()
        assert got is not None, (
            "expected partial index on dead_letter(resolved_at) WHERE resolved_at IS NULL"
        )


def test_words_type_check_rejects_invalid(at_0003):
    with at_0003.begin() as conn, pytest.raises(IntegrityError):
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.words (raw_form, normalized_form, type) VALUES ('x', 'x', 3)"
            )
        )


def test_stage_artifacts_cascade_on_word_delete(at_0003):
    with at_0003.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.words "
                "(id, raw_form, normalized_form, type) VALUES (50, 'x', 'x', 1)"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.stage_artifacts "
                "(word_id, stage_name, fingerprint, payload, source) "
                "VALUES (50, 'fetch_dict', 'fp', '{}'::jsonb, 'pipeline:x')"
            )
        )
    with at_0003.begin() as conn:
        conn.execute(sa.text("DELETE FROM pipeline.words WHERE id = 50"))
    with at_0003.connect() as conn:
        n = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.stage_artifacts WHERE word_id = 50")
        ).scalar()
        assert n == 0, "stage_artifacts should cascade when its pipeline word is deleted"


def test_dead_letter_word_id_has_no_fk(at_0003):
    """Audit retention: dead_letter.word_id logically points to pipeline.words(id)
    but has NO FK, so entries survive word deletion."""
    with at_0003.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.dead_letter "
                "(word_id, stage_name, error, attempt) "
                "VALUES (888888, 'fetch_dict', 'boom', 3)"
            )
        )
        n = conn.execute(
            sa.text("SELECT count(*) FROM pipeline.dead_letter WHERE word_id = 888888")
        ).scalar()
        assert n == 1
