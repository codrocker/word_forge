"""Minimal smoke endpoint for M1 — checks DB roundtrip."""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine

from wordforge.web.deps import get_engine
from wordforge.web.errors import envelope_ok

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health(engine: Engine = Depends(get_engine)):
    # 3s statement timeout avoids health checks blocking worker pool on slow DB
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '3s'"))
        conn.execute(text("SELECT 1"))
    return envelope_ok({"status": "ok"})
