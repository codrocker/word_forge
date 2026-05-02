"""Recover a wordforge Postgres from scratch using the momo MySQL source.

Replays the manual steps from the 2026-04-30 incident. One command takes
a freshly-empty database to a fully-populated one:

    1. `alembic upgrade head`                                       (schema)
    2. (optional) pg_restore / psql a cache SQL file                (free LLM replays)
    3. Dump momo MySQL `word` table → tsv (ORDER BY word_id)        (source of truth)
    4. Build word_list.txt (normalize + casefold, preserve order)   (ingest input)
    5. `wordforge ingest`                                           (121k pipeline.words)
    6. `wordforge run --batch --concurrency N`                      (let pipeline finish)

    domain.words.word_id is assigned by BIGSERIAL starting at 100001 (migration
    0005). Because step 3 dumps ORDER BY momo word_id and steps 4-5 preserve
    order, the resulting domain.words rows are roughly monotone in the same
    order as momo — exact alignment isn't required since we no longer pin
    external ids.

Inputs are via env or CLI flags (CLI wins). Each step is idempotent where
possible — re-running after a crash should resume, not duplicate.

SAFETY: this script runs `alembic upgrade head` which creates tables;
it does NOT drop anything. If you need to drop + recreate, do so
manually beforehand. Enforces the DATABASE_URL guard: refuses to touch
a DB whose name is `wordforge` *unless* --i-am-recovering-prod is given
(because recover = destructive enough to warrant a second switch).

Typical invocation::

    source ~/.wordforge/prod.env
    uv run python scripts/recover_from_momo.py \\
        --batch MOMO_RECOVERY \\
        --momo-host 120.27.242.42 \\
        --momo-user user_service_1 \\
        --momo-pw-env SAILING_MYSQL_PW \\
        --momo-db word \\
        --cache-backup /tmp/wordforge_smoke/cache_backup/llm_cache_pre_multicheck_full.sql \\
        --concurrency 30
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent


def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[recover {ts}] {msg}", flush=True)


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Run a shell command, stream stdout/err through."""
    _log(f"$ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=REPO_ROOT, check=True, **kw)


def _guard_database_url(confirmed: bool) -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        sys.exit("DATABASE_URL not set")
    cleaned = re.sub(r"^([a-z]+)\+[a-z]+://", r"\1://", url, count=1)
    try:
        host = (urlparse(cleaned).hostname or "").lower()
        db = (urlparse(cleaned).path or "").lstrip("/").split("?", 1)[0]
    except ValueError:
        sys.exit(f"unparseable DATABASE_URL: {url}")
    if db == "wordforge" and not confirmed:
        sys.exit(
            f"Refusing to run against production db name {db!r} on {host!r}. "
            "Pass --i-am-recovering-prod if this is intentional."
        )
    _log(f"target: host={host!r} db={db!r}")


def step_alembic_upgrade() -> None:
    _log("Step 1: alembic upgrade head")
    _run(["uv", "run", "alembic", "upgrade", "head"])


def step_restore_cache(cache_sql: Path | None) -> None:
    if cache_sql is None:
        _log("Step 2: skip cache restore (no --cache-backup given)")
        return
    if not cache_sql.is_file():
        sys.exit(f"cache backup not found: {cache_sql}")
    size_mb = cache_sql.stat().st_size // (1024 * 1024)
    _log(f"Step 2: restoring cache from {cache_sql} ({size_mb} MB)")
    # Route through docker exec so we don't need psql locally.
    pg_container = os.environ.get("PG_CONTAINER", "wordforge-pg")
    pg_user = os.environ.get("PG_USER", "wordforge")
    pg_db = os.environ.get("PG_DB", "wordforge")
    with cache_sql.open("rb") as f:
        subprocess.run(
            ["docker", "exec", "-i", pg_container, "psql", "-U", pg_user, "-d", pg_db],
            check=True,
            stdin=f,
            stdout=subprocess.DEVNULL,  # sql produces INSERT 0 1 × 300k; drop it
        )


def step_dump_momo(args: argparse.Namespace, workdir: Path) -> Path:
    _log("Step 3: dumping momo `word` table → tsv")
    pw = os.environ.get(args.momo_pw_env)
    if not pw:
        sys.exit(f"env {args.momo_pw_env} not set; put the MySQL password there")
    out = workdir / "momo_word_dump.tsv"
    with out.open("wb") as f:
        subprocess.run(
            [
                "mysql", "-h", args.momo_host,
                "-P", str(args.momo_port),
                "-u", args.momo_user,
                f"-p{pw}",
                "--connect-timeout=30", "-BN",
                "-e",
                f"USE {args.momo_db}; SELECT form, word_id, type FROM word ORDER BY word_id;",
            ],
            check=True,
            stdout=f,
        )
    _log(f"  wrote {out} ({out.stat().st_size // 1024} KB)")
    return out


def step_build_inputs(dump_tsv: Path, workdir: Path) -> Path:
    """Dump is ORDER BY word_id — preserve that order so subsequent
    BIGSERIAL assignment in domain.words.word_id stays roughly monotone
    against momo word_id.
    """
    _log("Step 4: build word_list.txt")
    word_list: list[str] = []
    seen: set[tuple[str, int]] = set()
    kept = 0
    skipped = 0
    with dump_tsv.open(encoding="utf-8") as f:
        for line in f:
            if line.startswith("mysql:"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            form, wid_s, type_s = parts[0], parts[1], parts[2]
            if not form or not wid_s or not type_s:
                continue
            try:
                type_ = int(type_s)
            except ValueError:
                continue
            normalized = form.strip().casefold()
            if not normalized:
                skipped += 1
                continue
            key = (normalized, type_)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            word_list.append(form)
            kept += 1
    out_list = workdir / "word_list.txt"
    out_list.write_text("\n".join(word_list) + "\n", encoding="utf-8")
    _log(f"  kept {kept}, skipped {skipped} case-collisions")
    return out_list


def step_ingest(word_list: Path, batch: str) -> None:
    _log(f"Step 5: wordforge ingest --batch {batch}")
    _run(["uv", "run", "wordforge", "ingest", str(word_list), "--batch", batch])


def step_run_pipeline(batch: str, concurrency: int) -> None:
    _log(f"Step 6: wordforge run --batch {batch} --concurrency {concurrency}")
    _run(
        ["uv", "run", "wordforge", "run",
         "--batch", batch, "--concurrency", str(concurrency)]
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--batch", required=True, help="batch_id, e.g. MOMO_RECOVERY")
    ap.add_argument("--momo-host", required=True)
    ap.add_argument("--momo-port", type=int, default=3306)
    ap.add_argument("--momo-user", required=True)
    ap.add_argument("--momo-pw-env", required=True,
                    help="env var name holding the MySQL password (so it's not in argv)")
    ap.add_argument("--momo-db", default="word")
    ap.add_argument("--cache-backup", type=Path, default=None,
                    help="optional .sql file of pipeline.external_call_cache to restore first")
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--workdir", type=Path,
                    default=Path("/tmp/wordforge_smoke/recovery"),
                    help="scratch dir for tsv / word_list / id_map")
    ap.add_argument("--i-am-recovering-prod", action="store_true",
                    help="required if target DATABASE_URL is the prod db (name 'wordforge')")
    ap.add_argument("--skip-pipeline", action="store_true",
                    help="stop after ingest; useful for dry-running")
    args = ap.parse_args()

    _guard_database_url(args.i_am_recovering_prod)

    args.workdir.mkdir(parents=True, exist_ok=True)
    step_alembic_upgrade()
    step_restore_cache(args.cache_backup)
    tsv = step_dump_momo(args, args.workdir)
    word_list = step_build_inputs(tsv, args.workdir)
    step_ingest(word_list, args.batch)
    if args.skip_pipeline:
        _log("--skip-pipeline set; stopping after ingest")
        return 0
    step_run_pipeline(args.batch, args.concurrency)
    _log("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
