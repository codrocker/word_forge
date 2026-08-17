"""Session lifecycle: create / validate / revoke / cleanup.

Cookie: `session=<raw_token>; HttpOnly; SameSite=Strict; Path=/api; Max-Age=604800; [Secure]`
DB stores only sha256(raw_token) in meta.editor_sessions.
"""
from __future__ import annotations

import datetime as _dt
import os
import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

from wordforge.web.auth import generate_session_token, hash_session_token

SESSION_TTL = _dt.timedelta(days=7)
COOKIE_NAME = "session"

# generate_session_token() emits secrets.token_urlsafe(32) ≈ 43 chars of
# [A-Za-z0-9_-]. Loose bounds reject malformed cookies before any crypto/DB
# work; the fullmatch guard also serves as the input sanitizer boundary for
# taint analysis.
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,128}")


def session_digest(raw_token: str | None) -> str | None:
    """Validate cookie shape at the trust boundary, then hash it.

    Returns None for missing/malformed tokens (early 401 — no DB roundtrip)
    and the sha256 digest otherwise. Callers pass only the digest onward.
    """
    if not raw_token or not _TOKEN_RE.fullmatch(raw_token):
        return None
    return hash_session_token(raw_token)


def cookie_secure() -> bool:
    """Per spec: default false for intranet HTTP; set true for TLS deploy."""
    return os.environ.get("WORDFORGE_WEB_COOKIE_SECURE", "false").lower() == "true"


def create_session(conn: Connection, editor_id: int) -> str:
    """Return raw token for cookie; hash is persisted in meta.editor_sessions."""
    raw, digest = generate_session_token()
    expires = _dt.datetime.now(_dt.timezone.utc) + SESSION_TTL
    conn.execute(
        text(
            "INSERT INTO meta.editor_sessions (token_hash, editor_id, expires_at) "
            "VALUES (:h, :e, :x)"
        ),
        {"h": digest, "e": editor_id, "x": expires},
    )
    return raw


def find_active_editor(conn: Connection, raw_token: str) -> dict | None:
    """Validate raw token against DB hash + expiry + editor is_active."""
    digest = hash_session_token(raw_token)
    row = conn.execute(
        text(
            "SELECT e.id, e.email, e.display_name, e.is_active "
            "FROM meta.editor_sessions s JOIN meta.editors e ON s.editor_id = e.id "
            "WHERE s.token_hash = :h AND s.expires_at > now()"
        ),
        {"h": digest},
    ).first()
    if row is None or not row.is_active:
        return None
    return {"id": row.id, "email": row.email, "display_name": row.display_name}


def revoke_session(conn: Connection, raw_token: str) -> None:
    conn.execute(
        text("DELETE FROM meta.editor_sessions WHERE token_hash = :h"),
        {"h": hash_session_token(raw_token)},
    )


def cleanup_expired(conn: Connection) -> None:
    """Opportunistic cleanup on login."""
    conn.execute(text("DELETE FROM meta.editor_sessions WHERE expires_at < now()"))
