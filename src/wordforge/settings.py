"""Read DATABASE_URL from env. Richer config (yaml merge) is P2's job."""

from __future__ import annotations

import os


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy wordforge/.env.example to .env "
            "and `export $(cat .env | xargs)` or set it in your shell."
        )
    return url
