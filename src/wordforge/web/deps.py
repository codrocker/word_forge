"""Dependencies: engine singleton + current editor."""
from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.engine import Engine

from wordforge.db.engine import make_engine
from wordforge.web.security import COOKIE_NAME, find_active_editor


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    # spec §4.5: web pool_size=5, max_overflow=5
    return make_engine(pool_size=5, max_overflow=5)


def dispose_engine() -> None:
    """Dispose cached engine and clear cache (tests + lifespan shutdown)."""
    if get_engine.cache_info().currsize > 0:
        get_engine().dispose()
    get_engine.cache_clear()


def current_editor(request: Request, engine: Engine = Depends(get_engine)) -> dict:
    """Require a valid session cookie; raise 401 otherwise."""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not logged in")
    with engine.connect() as conn:
        editor = find_active_editor(conn, raw)
    if editor is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    return editor
