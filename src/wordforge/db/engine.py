"""Thin factory for SQLAlchemy engines. Session management lives in P2."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from wordforge.settings import database_url


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url())
