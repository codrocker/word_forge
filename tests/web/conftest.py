"""web test fixtures."""
import subprocess
from pathlib import Path

import pytest

from wordforge.web.deps import dispose_engine
from wordforge.web.routes.auth import limiter

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session", autouse=True)
def _ensure_alembic_head():
    """Guarantee test DB is at head before any web test runs.

    Sibling tests in tests/db/ (test_app_schema, test_migrations, test_pipeline_schema)
    teardown with `alembic downgrade base`. When those run before web tests in the same
    pytest session, the DB ends up empty and web tests fail on missing meta.* / domain.*
    tables. This autouse session fixture runs `alembic upgrade head` once before the
    first web test collects.
    """
    subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=_REPO_ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture(autouse=True)
def _reset_web_state_between_tests():
    """Each web test starts with fresh Engine + empty rate-limit counters.

    Avoids pool/state bleed when multiple TestClient instances share the cached engine,
    and prevents slowapi in-memory limiter counters from leaking across tests.
    """
    dispose_engine()
    limiter.reset()
    yield
    dispose_engine()
