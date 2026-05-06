"""web test fixtures."""
import pytest

from wordforge.web.deps import dispose_engine


@pytest.fixture(autouse=True)
def _reset_engine_between_tests():
    """Ensure each web test starts with a fresh Engine singleton.

    Avoids pool/state bleed when multiple TestClient instances share the cached engine.
    """
    dispose_engine()
    yield
    dispose_engine()
