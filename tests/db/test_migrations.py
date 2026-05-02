"""0001 migration creates `app` and `pipeline` schemas and downgrade drops them."""

from __future__ import annotations

import os
import subprocess

import pytest
import sqlalchemy as sa

from wordforge.db.engine import make_engine

WORDFORGE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _alembic(*args: str) -> None:
    subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=WORDFORGE_DIR,
        check=True,
    )


@pytest.fixture
def fresh_db():
    """Downgrade to base before each test; upgrade as needed inside."""
    _alembic("downgrade", "base")
    yield
    _alembic("downgrade", "base")


def _has_schema(conn: sa.Connection, name: str) -> bool:
    return (
        conn.execute(
            sa.text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :n"),
            {"n": name},
        ).scalar()
        is not None
    )


def test_0001_upgrade_creates_schemas(fresh_db):
    # 0001 creates the schemas under their original names (app + pipeline).
    # The rename to `domain` happens later in 0007 — tests that stop at 0001
    # must assert the 0001-era name, not the post-rename name.
    _alembic("upgrade", "0001")
    engine = make_engine()
    try:
        with engine.connect() as conn:
            assert _has_schema(conn, "app")
            assert _has_schema(conn, "pipeline")
    finally:
        engine.dispose()


def test_0001_downgrade_drops_schemas(fresh_db):
    _alembic("upgrade", "0001")
    _alembic("downgrade", "base")
    engine = make_engine()
    try:
        with engine.connect() as conn:
            assert not _has_schema(conn, "app")
            assert not _has_schema(conn, "pipeline")
    finally:
        engine.dispose()


def test_full_upgrade_downgrade_cycle(fresh_db):
    _alembic("upgrade", "head")
    engine = make_engine()
    try:
        with engine.connect() as conn:
            for sch, tbl in [
                ("domain", "words"),
                ("domain", "meanings"),
                ("domain", "sentences"),
                ("domain", "mnemonics"),
                ("domain", "phrases"),
                ("pipeline", "batches"),
                ("pipeline", "words"),
                ("pipeline", "stage_artifacts"),
                ("pipeline", "stage_runs"),
                ("pipeline", "external_call_cache"),
                ("pipeline", "dead_letter"),
            ]:
                assert (
                    conn.execute(
                        sa.text(
                            "SELECT 1 FROM information_schema.tables "
                            "WHERE table_schema=:s AND table_name=:t"
                        ),
                        {"s": sch, "t": tbl},
                    ).scalar()
                    == 1
                ), f"{sch}.{tbl} missing after upgrade head"
    finally:
        engine.dispose()

    _alembic("downgrade", "base")
    engine = make_engine()
    try:
        with engine.connect() as conn:
            n = conn.execute(
                sa.text(
                    "SELECT count(*) FROM information_schema.schemata "
                    "WHERE schema_name IN ('domain','app','pipeline')"
                )
            ).scalar()
            assert n == 0, "schemas not fully dropped"
    finally:
        engine.dispose()

    _alembic("upgrade", "head")
