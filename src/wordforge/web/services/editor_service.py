"""Editor account CRUD — called by CLI; web admin does not expose registration."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from wordforge.web.auth import hash_password


def create_editor(engine: Engine, email: str, display_name: str, raw_password: str) -> int:
    pw_hash = hash_password(raw_password)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO meta.editors (email, display_name, password_hash) "
                "VALUES (:e, :d, :h) RETURNING id"
            ),
            {"e": email, "d": display_name, "h": pw_hash},
        ).first()
    return row.id


def list_editors(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT id, email, display_name, is_active, created_at "
                "FROM meta.editors ORDER BY id"
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def deactivate_editor(engine: Engine, email: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE meta.editors SET is_active = FALSE WHERE email = :e"),
            {"e": email},
        )
