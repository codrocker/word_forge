"""Engine factory must accept pool kwargs for web process sizing."""
from wordforge.db.engine import make_engine


def test_make_engine_accepts_pool_size(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test",
    )
    eng = make_engine(pool_size=3, max_overflow=2)
    assert eng.pool.size() == 3
    eng.dispose()
