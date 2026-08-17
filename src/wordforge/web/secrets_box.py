"""Fernet box for operator-entered provider keys.

The encryption key comes from the WORDFORGE_CONFIG_SECRET env var (set it
to a `Fernet.generate_key()` value; store it in ~/.wordforge/prod.env or
the deployment secret store — never in the repo). Plaintext keys exist
only in memory between the HTTP request and the encrypt() call; at rest
they are Fernet tokens, and no API response ever returns them.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


class SecretBoxError(RuntimeError):
    """Raised when encryption is requested but no secret is configured."""


def _fernet():
    import os

    secret = os.environ.get("WORDFORGE_CONFIG_SECRET", "")
    if not secret:
        raise SecretBoxError(
            "WORDFORGE_CONFIG_SECRET env var not set — generate with "
            "Fernet.generate_key() and put it in the wordforge env file "
            "before saving provider keys"
        )
    from cryptography.fernet import Fernet

    return Fernet(secret.encode())


def encrypt_key(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_key(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
