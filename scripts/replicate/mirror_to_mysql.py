"""PG domain.* -> MySQL word_forge mirror (one-shot).

Spec: docs/superpowers/specs/2026-05-02-dual-write-mysql-design.md
Plan: docs/superpowers/plans/2026-05-03-dual-write-mysql.md
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

# ruff: noqa: E501

TABLES = ["word", "meaning", "sentence", "mnemonic", "phrase"]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit(
            f"ERROR: missing env vars: {missing}.\n"
            f"  source ~/.wordforge/prod.env\n"
            f"  source ~/.wordforge/mysql_writer.env"
        )
    return {n: os.environ[n] for n in names}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Mirror wordforge domain.* -> MySQL word_forge.* "
        "via shadow tables + atomic RENAME swap.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Go through stage 0-2 (SELECT + build + INSERT into _shadow) "
        "but skip stage 3 (RENAME swap). Safe against live gozero reads.",
    )
    p.add_argument(
        "--run-log", type=Path, default=Path("./replicate_run.jsonl"),
        help="Where to append per-run sanity records.",
    )
    return p.parse_args(argv)


def _stage0_sanity(pg_engine, my_engine) -> None:
    """Verify every expected table + shadow exists; drop stale *_old if any."""
    _log("stage 0: sanity check PG + MySQL tables ...")
    with pg_engine.connect() as conn:
        for t in TABLES:
            pg_t = f"domain.{t}s" if t != "phrase" else "domain.phrases"
            conn.execute(text(f"SELECT 1 FROM {pg_t} LIMIT 0"))
    _log("  PG domain.* tables OK")

    with my_engine.begin() as conn:
        # main + shadow must exist
        for t in TABLES:
            for name in (t, f"{t}_shadow"):
                conn.execute(text(f"SELECT 1 FROM `{name}` LIMIT 0"))
        # clean up *_old leftovers from a crashed previous run
        rows = conn.execute(text("SHOW TABLES LIKE '%_old'")).all()
        for (name,) in rows:
            _log(f"  dropping stale {name}")
            conn.execute(text(f"DROP TABLE `{name}`"))
    _log("  MySQL main + shadow tables OK")


def _stage1_truncate_shadow(my_engine) -> None:
    _log("stage 1: TRUNCATE shadow tables ...")
    with my_engine.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"TRUNCATE TABLE `{t}_shadow`"))
    _log("  shadows cleared")


def _stage2_load_shadow(pg_engine, my_engine) -> dict[str, int]:
    raise NotImplementedError("Task 7")


def _stage3_swap(my_engine) -> None:
    raise NotImplementedError("Task 8")


def _stage4_count_check(pg_engine, my_engine, counts: dict[str, int], *, dry_run: bool) -> list[str]:
    raise NotImplementedError("Task 9")


def _stage5_summary(counts: dict[str, int], mismatches: list[str], run_log: Path, *, dry_run: bool) -> None:
    raise NotImplementedError("Task 9")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    env = _require_env("DATABASE_URL", "WORDFORGE_MYSQL_WRITER_DSN")
    _log(f"mode={'DRY-RUN (no RENAME)' if args.dry_run else 'LIVE'}")
    pg = create_engine(env["DATABASE_URL"], future=True)
    my = create_engine(env["WORDFORGE_MYSQL_WRITER_DSN"], future=True, pool_recycle=1800)
    try:
        _stage0_sanity(pg, my)
        _stage1_truncate_shadow(my)
        counts = _stage2_load_shadow(pg, my)
        if not args.dry_run:
            _stage3_swap(my)
        mismatches = _stage4_count_check(pg, my, counts, dry_run=args.dry_run)
        _stage5_summary(counts, mismatches, args.run_log, dry_run=args.dry_run)
        return 0
    finally:
        pg.dispose()
        my.dispose()


if __name__ == "__main__":
    sys.exit(main())
