"""Password hashing + session token helpers.

- argon2-cffi PasswordHasher (NOT passlib; see spec §4.1)
- token_hash = sha256(raw_token_urlsafe_32); raw token only in cookie, never in DB
- routes are sync def → argon2 runs in FastAPI threadpool (no asyncio.to_thread needed)
"""

from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(stored: str, raw: str) -> bool:
    try:
        _ph.verify(stored, raw)
    except VerifyMismatchError:
        return False
    return True


def generate_session_token() -> tuple[str, str]:
    """Return (raw_token_for_cookie, sha256_hex_for_db)."""
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
