"""Alembic env. Reads DATABASE_URL at runtime; no ORM metadata (raw DDL plan).

Production safety: refuses to run `downgrade` against a non-local host
unless the operator explicitly acknowledges via
WORDFORGE_CONFIRM_PROD_DOWNGRADE=yes. Complements the pytest conftest
guard — that one blocks pytest, this one blocks human-initiated
`alembic downgrade <rev>`.

The DB wipe incident (2026-04-30) happened because pytest ran downgrade
against the main DB. An operator typo (e.g. `alembic downgrade base`
meant for the test DB but shell pointed at prod) would have the same
blast radius. This guard prevents that specific pathology.
"""

from __future__ import annotations

import os
import re
import sys
from logging.config import fileConfig
from urllib.parse import urlparse

from alembic import context
from sqlalchemy import engine_from_config, pool

from wordforge.settings import database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

_DB_URL = database_url()
config.set_main_option("sqlalchemy.url", _DB_URL)

target_metadata = None  # raw DDL migrations; no autogenerate needed


_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _is_local_host(url: str) -> bool:
    if url.startswith("sqlite"):
        return True
    cleaned = re.sub(r"^([a-z]+)\+[a-z]+://", r"\1://", url, count=1)
    try:
        host = (urlparse(cleaned).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOCAL_HOSTS


def _guard_prod_downgrade() -> None:
    """Abort if this invocation is `downgrade` against a non-local DB
    and WORDFORGE_CONFIRM_PROD_DOWNGRADE is not set to 'yes'.

    `alembic` sets `config.cmd_opts` when invoked from the CLI; `.cmd`
    there is a 3-tuple whose first element is the command function
    (its __name__ is 'downgrade' / 'upgrade' / ...). Offline mode may
    not set cmd_opts, so we fall back to sys.argv scanning — safer to
    overshoot than to miss a downgrade.
    """
    if os.environ.get("WORDFORGE_CONFIRM_PROD_DOWNGRADE") == "yes":
        return
    if _is_local_host(_DB_URL):
        return

    cmd_name = ""
    cmd_opts = getattr(config, "cmd_opts", None)
    if cmd_opts is not None and getattr(cmd_opts, "cmd", None):
        try:
            cmd_name = cmd_opts.cmd[0].__name__  # type: ignore[attr-defined]
        except (AttributeError, IndexError, TypeError):
            cmd_name = ""
    if not cmd_name:
        # Fallback: scan argv. Catches `alembic downgrade base` even in
        # offline mode where cmd_opts is bare.
        argv = " ".join(sys.argv).lower()
        if "downgrade" in argv:
            cmd_name = "downgrade"

    if cmd_name != "downgrade":
        return

    host = urlparse(
        re.sub(r"^([a-z]+)\+[a-z]+://", r"\1://", _DB_URL, count=1)
    ).hostname or "?"
    print(
        "\n"
        "=============================================================\n"
        "REFUSING `alembic downgrade` AGAINST A NON-LOCAL DATABASE.\n"
        f"  target host: {host}\n"
        "\n"
        "Downgrade DROPs tables. If you really intend to downgrade a\n"
        "remote (prod/dev) DB, set:\n"
        "  WORDFORGE_CONFIRM_PROD_DOWNGRADE=yes alembic downgrade ...\n"
        "\n"
        "Otherwise fix your DATABASE_URL (~/.wordforge/prod.env vs the\n"
        "local docker test instance).\n"
        "=============================================================\n",
        file=sys.stderr,
    )
    sys.exit(2)


_guard_prod_downgrade()


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Lets a restricted account keep the version table in a schema it
            # owns (set version_table_schema in alembic.ini); default public.
            version_table_schema=config.get_main_option("version_table_schema"),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
