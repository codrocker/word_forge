"""Dependencies: engine singleton + current editor placeholder."""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy.engine import Engine

from wordforge.db.engine import make_engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    # spec §4.5: web pool_size=5, max_overflow=5
    return make_engine(pool_size=5, max_overflow=5)
