"""Mirror momo MySQL `package_new / package_unit / package_word` -> domain.*.

Full-refresh design:
- TRUNCATE all three domain tables, then COPY rows in dependency order
  (package → package_unit → package_word). Idempotent: re-running gives
  the same result, no merge/diff logic needed.
- word_count override: momo's `package_new.word_count` is planner-declared
  and 78 % unreliable (see _fixup_word_count), so after COPY we overwrite
  it with the actual COUNT(*) from domain.package_word. From here on
  wordforge is authoritative for word_count; momo is reference only.
- word_id mapping: subtract 999_900_000 so every package_word.word_id
  slots into wordforge's 10^5 ID range (matches migration 0006).
- Reserved-word rename: MySQL `order` column → PG `sort_order`
  (per word_lib_db_migration.md).
- Referential integrity: asserted in MySQL before mirror starts
  (we've already verified 0 orphans; re-check every run).
- Timestamps: MySQL has `create_time/update_time` (TIMESTAMP) for unit
  and package_word, and `create_at/update_at` (BIGINT epoch SECONDS,
  despite misleading names on the momo side) for package_new. We store
  them on `domain.package` as `created_at/updated_at` BIGINT seconds
  (per migration 0010 — matches backend contract). TIMESTAMP rows from
  package_unit/package_word are dropped; they default to now() on
  insert, which is fine because those timestamps are mirror-run
  metadata anyway.

Run via:
  source ~/.wordforge/momo.env
  export DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge'
  uv run python scripts/mirror_momo_packages.py

Safety:
- pg engine guard: refuses to run if DATABASE_URL host is an RDS
  endpoint and --i-am-mirroring-prod is not passed.
- Pre-flight counts + post-flight cross-check in one console block
  so operator can eyeball drift.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# ruff: noqa: E501 — long SQL for readability


WORDFORGE_WORD_ID_SHIFT = 999_900_000


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _guard_prod(db_url: str, flag: bool) -> None:
    """Refuse to run against RDS unless operator explicitly opts in."""
    if "aliyuncs.com" in db_url and not flag:
        sys.exit(
            "ERROR: DATABASE_URL points at Ali Cloud RDS. "
            "Pass --i-am-mirroring-prod to continue."
        )


def mirror(args: argparse.Namespace) -> int:
    import pymysql
    import sqlalchemy as sa

    pg_url = os.environ.get("DATABASE_URL")
    if not pg_url:
        sys.exit("DATABASE_URL not set")
    _guard_prod(pg_url, args.i_am_mirroring_prod)

    for var in ("MOMO_MYSQL_HOST", "MOMO_MYSQL_PORT", "MOMO_MYSQL_USER",
                "MOMO_MYSQL_PASSWORD", "MOMO_MYSQL_DB"):
        if not os.environ.get(var):
            sys.exit(f"env {var} not set — `source ~/.wordforge/momo.env` first")

    mysql = pymysql.connect(
        host=os.environ["MOMO_MYSQL_HOST"], port=int(os.environ["MOMO_MYSQL_PORT"]),
        user=os.environ["MOMO_MYSQL_USER"], password=os.environ["MOMO_MYSQL_PASSWORD"],
        database=os.environ["MOMO_MYSQL_DB"], connect_timeout=30,
        cursorclass=pymysql.cursors.SSCursor,  # server-side cursor for big table streaming
    )
    engine = sa.create_engine(pg_url, future=True)

    try:
        _preflight_check(mysql)
        _log("pre-flight OK")
        with engine.begin() as pg:
            _truncate(pg)
            n_pkg = _copy_package(mysql, pg)
            n_unit = _copy_package_unit(mysql, pg)
            n_pw = _copy_package_word(mysql, pg)
            n_fixed = _fixup_word_count(pg)
        _log(f"done: package={n_pkg} unit={n_unit} package_word={n_pw} "
             f"word_count_fixed={n_fixed}")
        _postflight_check(engine, n_pkg, n_unit, n_pw)
    finally:
        mysql.close()
        engine.dispose()
    return 0


def _preflight_check(mysql) -> None:
    import pymysql
    # re-use a buffered cursor for count queries (SSCursor doesn't rewind)
    buf = mysql.cursor(pymysql.cursors.Cursor)
    try:
        checks = [
            ("package_unit orphans",
             "SELECT COUNT(*) FROM package_unit pu "
             "WHERE NOT EXISTS (SELECT 1 FROM package_new p WHERE p.package_id = pu.package_id)"),
            ("package_word orphans -> package",
             "SELECT COUNT(*) FROM package_word pw "
             "WHERE NOT EXISTS (SELECT 1 FROM package_new p WHERE p.package_id = pw.package_id)"),
            ("package_word orphans -> unit",
             "SELECT COUNT(*) FROM package_word pw "
             "WHERE NOT EXISTS (SELECT 1 FROM package_unit u WHERE u.unit_id = pw.unit_id)"),
        ]
        for name, q in checks:
            buf.execute(q)
            n = buf.fetchone()[0]
            if n != 0:
                sys.exit(f"pre-flight FAIL: {name} = {n}; aborting mirror")
            _log(f"  check {name}: 0 ✓")
    finally:
        buf.close()


def _truncate(pg) -> None:
    import sqlalchemy as sa
    # PG requires every table referenced by an FK to be in the same TRUNCATE
    # statement (or use CASCADE). Single-statement form is cleaner than
    # CASCADE — explicit about the blast radius, and fails fast if a new
    # referencing table gets added without updating this list.
    pg.execute(sa.text(
        "TRUNCATE TABLE domain.package_word, domain.package_unit, domain.package"
    ))
    _log("  truncated domain.package_word / package_unit / package")


def _copy_package(mysql, pg) -> int:
    import sqlalchemy as sa
    cur = mysql.cursor()
    cur.execute(
        "SELECT package_id, type, category_code, title, word_count, intro, "
        "author, isbn, publisher, org, version, score, status, create_at, update_at "
        "FROM package_new"
    )
    rows = [
        dict(
            package_id=r[0], type=r[1], category_code=r[2], title=r[3],
            word_count=r[4], intro=r[5], author=r[6], isbn=r[7],
            publisher=r[8], org=r[9], version=r[10], score=r[11],
            status=r[12], created_at=r[13], updated_at=r[14],
        )
        for r in cur
    ]
    if rows:
        pg.execute(
            sa.text(
                "INSERT INTO domain.package "
                "(package_id, type, category_code, title, word_count, intro, "
                "author, isbn, publisher, org, version, score, status, created_at, updated_at) "
                "VALUES (:package_id, :type, :category_code, :title, :word_count, :intro, "
                ":author, :isbn, :publisher, :org, :version, :score, :status, :created_at, :updated_at)"
            ),
            rows,
        )
    _log(f"  copied {len(rows)} rows → domain.package")
    return len(rows)


def _copy_package_unit(mysql, pg) -> int:
    import sqlalchemy as sa
    cur = mysql.cursor()
    cur.execute(
        "SELECT unit_id, package_id, title, `order` FROM package_unit"
    )
    rows = [
        dict(unit_id=r[0], package_id=r[1], title=r[2], sort_order=float(r[3]))
        for r in cur
    ]
    CHUNK = 5000
    for i in range(0, len(rows), CHUNK):
        pg.execute(
            sa.text(
                "INSERT INTO domain.package_unit (unit_id, package_id, title, sort_order) "
                "VALUES (:unit_id, :package_id, :title, :sort_order)"
            ),
            rows[i:i + CHUNK],
        )
    _log(f"  copied {len(rows)} rows → domain.package_unit")
    return len(rows)


def _copy_package_word(mysql, pg) -> int:
    import sqlalchemy as sa
    cur = mysql.cursor()
    cur.execute(
        "SELECT p_word_id, package_id, unit_id, word_id, `order`, importance "
        "FROM package_word"
    )
    CHUNK = 5000
    batch: list[dict] = []
    total = 0
    for r in cur:
        batch.append(dict(
            p_word_id=r[0], package_id=r[1], unit_id=r[2],
            word_id=r[3] - WORDFORGE_WORD_ID_SHIFT,   # shift into 10^5 range
            sort_order=float(r[4]), importance=r[5],
        ))
        if len(batch) >= CHUNK:
            pg.execute(
                sa.text(
                    "INSERT INTO domain.package_word "
                    "(p_word_id, package_id, unit_id, word_id, sort_order, importance) "
                    "VALUES (:p_word_id, :package_id, :unit_id, :word_id, :sort_order, :importance)"
                ),
                batch,
            )
            total += len(batch)
            batch.clear()
            if total % 50000 == 0:
                _log(f"    streamed {total} rows → domain.package_word")
    if batch:
        pg.execute(
            sa.text(
                "INSERT INTO domain.package_word "
                "(p_word_id, package_id, unit_id, word_id, sort_order, importance) "
                "VALUES (:p_word_id, :package_id, :unit_id, :word_id, :sort_order, :importance)"
            ),
            batch,
        )
        total += len(batch)
    _log(f"  copied {total} rows → domain.package_word")
    return total


def _fixup_word_count(pg) -> int:
    """Override momo's package.word_count with wordforge's real package_word count.

    momo's `package_new.word_count` is a planner-declared value, not a
    maintained counter — 78 % (1,124 / 1,443) of packages disagreed with
    the actual row count on first mirror (485 declared several-thousand
    words but had zero rows; 626 had MORE rows than declared). Since
    backend consumes serving.word_payload as canonical, wordforge owns
    the count: after COPY we overwrite word_count to match reality. Momo
    stays source-of-truth for everything else; word_count is wordforge-
    authoritative from here on.

    Returns number of rows updated (= packages whose declared value was
    wrong).
    """
    import sqlalchemy as sa
    r = pg.execute(sa.text(
        """
        UPDATE domain.package p
        SET word_count = sub.actual
        FROM (
            SELECT p2.package_id, COUNT(pw.p_word_id) AS actual
            FROM domain.package p2
            LEFT JOIN domain.package_word pw USING (package_id)
            GROUP BY p2.package_id
        ) sub
        WHERE p.package_id = sub.package_id
          AND p.word_count <> sub.actual
        """
    ))
    _log(f"  fix-up: rewrote word_count on {r.rowcount} packages "
         f"(momo declared != wordforge actual)")
    return r.rowcount


def _postflight_check(engine, n_pkg: int, n_unit: int, n_pw: int) -> None:
    import sqlalchemy as sa
    with engine.connect() as pg:
        for tbl, expected in [
            ("domain.package", n_pkg),
            ("domain.package_unit", n_unit),
            ("domain.package_word", n_pw),
        ]:
            # Binding a schema-qualified identifier is awkward; the name is
            # compile-time constant from our own loop so the f-string is safe.
            got = pg.execute(sa.text(f"SELECT COUNT(*) FROM {tbl}")).scalar()
            assert got == expected, f"mismatch {tbl}: pg={got} mysql={expected}"
            _log(f"  post-flight {tbl}: {got} rows ✓")
        # word_id range after shift
        rng = pg.execute(sa.text(
            "SELECT MIN(word_id), MAX(word_id) FROM domain.package_word"
        )).first()
        _log(f"  post-flight word_id range: [{rng[0]}, {rng[1]}] (expect 100001..~222665)")
        # referential consistency: package_word.word_id vs domain.words.word_id
        # (note: we allow missing words because wordforge may not have them yet)
        missing = pg.execute(sa.text(
            "SELECT COUNT(DISTINCT pw.word_id) FROM domain.package_word pw "
            "WHERE NOT EXISTS (SELECT 1 FROM domain.words w WHERE w.word_id = pw.word_id)"
        )).scalar()
        _log(f"  package_word.word_id not in domain.words: {missing} distinct words")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--i-am-mirroring-prod", action="store_true",
                   help="required if DATABASE_URL points at aliyuncs.com RDS")
    args = p.parse_args()
    return mirror(args)


if __name__ == "__main__":
    sys.exit(main())
