"""Thin factory for SQLAlchemy engines. Session management lives in P2."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from wordforge.settings import database_url


def make_engine(url: str | None = None, **engine_kwargs) -> Engine:
    """Create a sync SQLAlchemy Engine.

    engine_kwargs forwarded to create_engine (pool_size, max_overflow, etc.).
    Web process passes pool_size=5, max_overflow=5.
    """
    return create_engine(url or database_url(), **engine_kwargs)
