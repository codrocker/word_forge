"""Tests for the DATABASE_URL guard in tests/conftest.py.

The guard refuses to let pytest run against anything that isn't clearly
a disposable local test DB. Pure-function test on _looks_like_test_db
— no DB required, so this module is safe even when the guard itself is
refusing the session (the unit runs before any fixture is requested).
"""

from __future__ import annotations

import pytest

# Import from the conftest in the same package
from tests.conftest import _looks_like_test_db


@pytest.mark.parametrize(
    "url,expected_ok",
    [
        # Pass: local + 'test' in db name
        ("postgresql+psycopg://u:p@localhost:5433/wordforge_test", True),
        ("postgresql+psycopg://u:p@127.0.0.1:5433/wordforge_test", True),
        ("postgresql+psycopg://u:p@localhost:5433/test_db", True),
        ("postgresql://u:p@localhost:5432/anything_test_anything", True),
        ("sqlite:///:memory:", True),
        ("sqlite:///tmp/test.db", True),
        # Fail: production DB name regardless of surroundings
        ("postgresql+psycopg://u:p@localhost:5433/wordforge", False),
        ("postgresql+psycopg://u:p@localhost:5433/wordforge_prod", False),
        ("postgresql+psycopg://u:p@localhost:5433/wordforge_dev", False),
        ("postgresql+psycopg://u:p@127.0.0.1:5433/WORDFORGE", False),   # case-insensitive
        # Fail: non-local host, even with 'test' in db name
        (
            "postgresql+psycopg://u:p@rm-cn-0fi4riz180001nyo.rwlb.rds.aliyuncs.com:5432/wordforge_test",
            False,
        ),
        ("postgresql+psycopg://u:p@10.0.0.1:5432/wordforge_test", False),
        ("postgresql+psycopg://u:p@db.example.com:5432/wordforge_test", False),
        # Fail: local but db name doesn't contain 'test'
        ("postgresql+psycopg://u:p@localhost:5433/staging_copy", False),
        ("postgresql+psycopg://u:p@localhost:5433/prod_clone", False),
    ],
)
def test_guard_accept_reject(url: str, expected_ok: bool) -> None:
    ok, reason = _looks_like_test_db(url)
    assert ok is expected_ok, f"url={url!r} expected ok={expected_ok} got {ok} ({reason})"
