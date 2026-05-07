"""rebuild_word_payload: status=1 upserts serving; status=0/2 deletes."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import text

from wordforge.db.engine import make_engine

# Guard against DB state pollution from sibling tests that downgrade base
# (e.g. test_app_schema / test_migrations teardown `alembic downgrade base`).
# Ensure the test DB is at head before we run.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_head() -> None:
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=_REPO_ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def engine():
    _ensure_head()
    eng = make_engine()
    yield eng
    eng.dispose()


def _insert_min_word(conn, status: int) -> int:
    row = conn.execute(
        text(
            "INSERT INTO domain.words "
            "(type, form, phonetic_us, phonetic_uk, source, status) "
            "VALUES (1, :form, '/t/', '/t/', 'human:test', :s) "
            "RETURNING word_id"
        ),
        {"form": f"testword_serving_{status}", "s": status},
    ).first()
    return row.word_id


def _cleanup(engine, word_id):
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM serving.word_payload WHERE word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM domain.words WHERE word_id = :w"), {"w": word_id})


def test_rebuild_status_1_upserts(engine):
    from wordforge.db.serving import rebuild_word_payload

    word_id = None
    try:
        with engine.begin() as conn:
            word_id = _insert_min_word(conn, status=1)
            rebuild_word_payload(conn, word_id)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT payload FROM serving.word_payload WHERE word_id = :w"),
                {"w": word_id},
            ).first()
        assert row is not None, "status=1 should upsert serving row"
        assert row.payload["status"] == 1
        assert row.payload["quality_flag"] == "none"
    finally:
        if word_id:
            _cleanup(engine, word_id)


def test_rebuild_status_2_deletes(engine):
    from wordforge.db.serving import rebuild_word_payload

    word_id = None
    try:
        with engine.begin() as conn:
            word_id = _insert_min_word(conn, status=2)
            # Insert a serving row to simulate previously-published word
            conn.execute(
                text(
                    "INSERT INTO serving.word_payload "
                    "(word_id, form, type, payload, updated_at) "
                    "VALUES (:w, 'x', 1, :p, now())"
                ),
                {"w": word_id, "p": '{"status": 1}'},
            )
        with engine.begin() as conn:
            rebuild_word_payload(conn, word_id)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM serving.word_payload WHERE word_id = :w"),
                {"w": word_id},
            ).first()
        assert row is None, "status=2 should delete serving row"
    finally:
        if word_id:
            _cleanup(engine, word_id)


def test_rebuild_status_0_deletes(engine):
    from wordforge.db.serving import rebuild_word_payload

    word_id = None
    try:
        with engine.begin() as conn:
            word_id = _insert_min_word(conn, status=0)
            conn.execute(
                text(
                    "INSERT INTO serving.word_payload "
                    "(word_id, form, type, payload, updated_at) "
                    "VALUES (:w, 'x', 1, :p, now())"
                ),
                {"w": word_id, "p": '{}'},
            )
        with engine.begin() as conn:
            rebuild_word_payload(conn, word_id)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM serving.word_payload WHERE word_id = :w"),
                {"w": word_id},
            ).first()
        assert row is None
    finally:
        if word_id:
            _cleanup(engine, word_id)


def test_rebuild_nonexistent_word_deletes(engine):
    """Hard-deleted word should also remove orphan serving row."""
    from wordforge.db.serving import rebuild_word_payload

    fake_word_id = 999999999
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO serving.word_payload "
                    "(word_id, form, type, payload, updated_at) "
                    "VALUES (:w, 'orphan', 1, :p, now()) ON CONFLICT DO NOTHING"
                ),
                {"w": fake_word_id, "p": '{}'},
            )
        with engine.begin() as conn:
            rebuild_word_payload(conn, fake_word_id)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT 1 FROM serving.word_payload WHERE word_id = :w"),
                {"w": fake_word_id},
            ).first()
        assert row is None
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM serving.word_payload WHERE word_id = :w"),
                {"w": fake_word_id},
            )
