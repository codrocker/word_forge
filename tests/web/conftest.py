"""web test fixtures."""
import pytest

from wordforge.web.deps import dispose_engine
from wordforge.web.routes.auth import limiter


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
