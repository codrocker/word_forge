"""Shared pytest fixtures for wordforge tests.

`at_head` migrates to head, yields a live engine, then downgrades to base.
Used by cache / llm / sources tests that need real Postgres. P1 migration
tests keep their own `fresh_db` fixture (different semantics — they control
migrations step-by-step).

SAFETY: This file does `alembic downgrade base` which DROPs every table in
`app.*` and `pipeline.*` schemas. Historically we have nuked the dev DB by
running pytest against the main DATABASE_URL. Prod now lives on Ali Cloud
RDS and must be PHYSICALLY unreachable to pytest:

  prod → Ali Cloud RDS (rm-cn-*.rwlb.rds.aliyuncs.com)  ← pytest REFUSED
  dev  → Ali Cloud RDS (same)                            ← pytest REFUSED
  test → local docker :5433 / wordforge_test             ← pytest ALLOWED

The guard below enforces: hostname must be localhost/127.0.0.1 AND
database name must contain 'test'. Anything else aborts pytest.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest
import sqlalchemy as sa

from wordforge.db.engine import make_engine
from wordforge.settings import database_url

WORDFORGE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}

# Exact database names that are always refused, regardless of host, to
# defend against "wordforge_testing_prod" / "wordforge_backup_test" style
# mixups. If we ever legitimately want a DB with one of these names, we
# explicitly remove it from the blacklist (forcing a review).
_DB_NAME_BLACKLIST = frozenset({"wordforge", "wordforge_prod", "wordforge_dev"})


def _looks_like_test_db(url: str) -> tuple[bool, str]:
    """Return (ok, reason). Accept only local + test-named DBs that are
    NOT on the hard blacklist.

    A URL passes iff ALL of:
      - it's sqlite :memory: / file URI, OR (scheme postgresql-ish AND
        host ∈ {localhost, 127.0.0.1, ::1})
      - database name is not in _DB_NAME_BLACKLIST
      - database name contains 'test' (for non-sqlite)
    """
    if url.startswith("sqlite"):
        return True, "sqlite"
    # strip sqlalchemy driver suffix (e.g. postgresql+psycopg → postgresql)
    cleaned = re.sub(r"^([a-z]+)\+[a-z]+://", r"\1://", url, count=1)
    try:
        p = urlparse(cleaned)
    except ValueError:
        return False, "unparseable url"
    host = (p.hostname or "").lower()
    if host not in _LOCAL_HOSTS:
        return False, f"host {host!r} is not local (prod is on Ali RDS)"
    db_name = (p.path or "").lstrip("/").split("?", 1)[0]
    db_lower = db_name.lower()
    if db_lower in _DB_NAME_BLACKLIST:
        return False, f"db name {db_name!r} is on the hard blacklist"
    if "test" not in db_lower:
        return False, f"db name {db_name!r} does not contain 'test'"
    return True, "local+test-named"


@pytest.fixture(scope="session", autouse=True)
def _guard_against_prod_db() -> None:
    """Refuse pytest unless DATABASE_URL is local-and-test-named.

    No escape hatch: prod is on Ali Cloud RDS so a different hostname. A
    local docker test DB is always reachable (`docker compose up -d` +
    `createdb wordforge_test`). No reason to ever bypass.
    """
    url = database_url()
    ok, reason = _looks_like_test_db(url)
    if ok:
        return
    host_port = url.rsplit("@", 1)[-1] if "@" in url else url
    msg = (
        "\n\n"
        "=============================================================\n"
        "REFUSING TO RUN PYTEST AGAINST A NON-TEST DATABASE.\n"
        "\n"
        f"  rejected: {reason}\n"
        f"  target:   {host_port}\n"
        "\n"
        "pytest's at_head fixture runs `alembic downgrade base` which\n"
        "DROPs every table in app.* and pipeline.*. This is safe ONLY\n"
        "against a disposable local docker database.\n"
        "\n"
        "To run tests:\n"
        "  docker compose up -d\n"
        "  docker exec wordforge-pg createdb -U wordforge wordforge_test\n"
        "  export DATABASE_URL=postgresql+psycopg://wordforge:wordforge@\\\n"
        "     localhost:5433/wordforge_test\n"
        "  uv run pytest\n"
        "=============================================================\n"
    )
    print(msg, file=sys.stderr, flush=True)
    pytest.exit(msg, returncode=2)


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
