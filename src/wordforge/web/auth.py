"""Password hashing + session token helpers.

- argon2-cffi PasswordHasher (NOT passlib; see spec §4.1)
- token_hash = sha256(raw_token_urlsafe_32); raw token only in cookie, never in DB
- routes are sync def → argon2 runs in FastAPI threadpool (no asyncio.to_thread needed)
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_ph = PasswordHasher()
logger = logging.getLogger(__name__)


def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(stored: str, raw: str) -> bool:
    try:
        _ph.verify(stored, raw)
    except VerifyMismatchError:
        return False
    except (VerificationError, InvalidHashError):
        # Corrupted/unrecognized hash format — don't leak details to caller.
        logger.warning("password verification failed due to malformed/corrupted hash")
        return False
    return True


# Dummy hash used by auth routes to equalize timing between
# account-exists and account-not-found paths (see spec §4.1).
# Computed once at import, re-used on every failed-lookup login.
_DUMMY_HASH = _ph.hash("constant-time-dummy-placeholder")


def get_dummy_hash() -> str:
    return _DUMMY_HASH


def generate_session_token() -> tuple[str, str]:
    """Return (raw_token_for_cookie, sha256_hex_for_db)."""
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
