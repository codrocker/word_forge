"""engine.make_engine() returns a live SQLAlchemy engine bound to DATABASE_URL."""

import pytest
import sqlalchemy as sa

from wordforge.db.engine import make_engine


@pytest.fixture(autouse=True)
def _set_url(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge",
    )


def test_make_engine_connects():
    engine = make_engine()
    try:
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT 1")).scalar() == 1
    finally:
        engine.dispose()
