"""PG vs MySQL count + checksum drift verifier.

Spec §7.2.  Prints a one-row summary per table; exits non-zero if any
count mismatches (checksum diffs are reported but not fatal because the
two sides use different hash functions).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

# ruff: noqa: E501

TABLES = [
    # (name, pg_table, key_cols_concat_expr_pg, key_cols_concat_expr_mysql)
    ("word",     "domain.words",     "word_id || '|' || form || '|' || type || '|' || coalesce(source, '')",
                                    "CONCAT_WS('|', word_id, form, type, IFNULL(source, ''))"),
    ("meaning",  "domain.meanings",  "meaning_id || '|' || word_id || '|' || coalesce(pos::text, '') || '|' || coalesce(cn_paraphrase, '')",
                                    "CONCAT_WS('|', meaning_id, word_id, IFNULL(pos, ''), IFNULL(cn_paraphrase, ''))"),
    # domain.sentences 没有 word_id 列; 用 JOIN 到 meanings 拿 word_id
    ("sentence",
     "(SELECT s.sentence_id, m.word_id, s.form FROM domain.sentences s "
     "JOIN domain.meanings m ON s.meaning_id = m.meaning_id) sv",
     "sentence_id || '|' || word_id || '|' || coalesce(form, '')",
     "CONCAT_WS('|', sentence_id, word_id, IFNULL(form, ''))"),
    ("mnemonic", "domain.mnemonics", "mnemonic_id || '|' || word_id || '|' || type::text",
                                    "CONCAT_WS('|', mnemonic_id, word_id, type)"),
    ("phrase",   "domain.phrases",   "phrase_id || '|' || form",
                                    "CONCAT_WS('|', phrase_id, form)"),
]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", type=Path, default=Path("./drift_report.jsonl"))
    args = p.parse_args()
    pg_url = os.environ.get("DATABASE_URL")
    my_url = os.environ.get("WORDFORGE_MYSQL_READER_DSN")
    if not pg_url or not my_url:
        sys.exit("ERROR: need DATABASE_URL + WORDFORGE_MYSQL_READER_DSN")
    pg = create_engine(pg_url, future=True)
    my = create_engine(my_url, future=True)
    has_drift = False
    records = []
    try:
        with pg.connect() as pgc, my.connect() as myc:
            for name, pg_table, pg_concat, my_concat in TABLES:
                pg_n = pgc.execute(text(f"SELECT count(*) FROM {pg_table}")).scalar_one()
                my_n = myc.execute(text(f"SELECT count(*) FROM `{name}`")).scalar_one()
                pg_sum = pgc.execute(text(
                    f"SELECT md5(string_agg({pg_concat}, ',' ORDER BY 1)) FROM {pg_table}"
                )).scalar_one() if pg_n else None
                my_sum = myc.execute(text(
                    f"SELECT HEX(BIT_XOR(CAST(CRC32({my_concat}) AS UNSIGNED))) FROM `{name}`"
                )).scalar_one() if my_n else None
                drift = pg_n != my_n
                status = "DRIFT" if drift else "OK"
                _log(f"  {status} {name}: pg={pg_n} mysql={my_n} pg_md5={pg_sum} my_xor_crc32={my_sum}")
                records.append({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "table": name,
                    "pg_count": pg_n, "mysql_count": my_n,
                    "pg_md5": pg_sum, "mysql_xor_crc32": my_sum,
                    "drift": drift,
                })
                if drift:
                    has_drift = True
    finally:
        pg.dispose()
        my.dispose()

    with args.report.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if has_drift:
        print("DRIFT: count mismatch found, see drift_report.jsonl", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
