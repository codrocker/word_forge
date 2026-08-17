"""PG domain.* -> MySQL word_forge mirror.

Full-refresh via shadow tables + atomic RENAME swap. Re-runnable; each run
rebuilds the MySQL side from scratch against the current PG state.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

from scripts.replicate.field_mapping import (
    row_to_mysql_meaning,
    row_to_mysql_mnemonic,
    row_to_mysql_phrase,
    row_to_mysql_sentence,
    row_to_mysql_word,
)

# ruff: noqa: E501

TABLES = ["word", "meaning", "sentence", "mnemonic", "phrase"]

_WORD_COLS = "word_id, type, form, phonetic_us, audio_us, phonetic_uk, audio_uk, source, status"
_MEANING_COLS = (
    "meaning_id, word_id, pos, pos_sub, equivalents, synonyms, antonyms, "
    "cn_paraphrase, en_paraphrase, source"
)
# domain.sentences 无 word_id / audio_* 列; 通过 JOIN domain.meanings 拿 word_id.
# citation / citation_detail PG 就有,直接用.
_SENTENCE_SELECT = (
    "SELECT s.sentence_id, m.word_id, s.meaning_id, s.form, s.translation, "
    "s.highlight, s.source, s.citation, s.citation_detail "
    "FROM domain.sentences s "
    "JOIN domain.meanings m ON s.meaning_id = m.meaning_id "
    "ORDER BY s.sentence_id"
)
_MNEMONIC_COLS = "mnemonic_id, word_id, type, content, source"
_PHRASE_COLS = "phrase_id, owner_word_id AS word_id, form, meaning"


def _insert_sql(table: str, cols: list[str]) -> str:
    placeholders = ", ".join(f":{c}" for c in cols)
    collist = ", ".join(f"`{c}`" for c in cols)
    return f"INSERT INTO `{table}_shadow` ({collist}) VALUES ({placeholders})"


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


def _load_relations_map(pg_engine) -> dict[str, dict[int, list[int]]]:
    """Build three word_id -> [child_id,...] maps for word table's JSON cols.

    Single SELECT per child table, ORDER BY child_id ASC so the resulting
    JSON arrays are stable across runs (helps checksum parity).
    """
    _log("  loading relation maps (word -> meanings/mnemonics/phrases) ...")
    out: dict[str, dict[int, list[int]]] = {"meanings": {}, "mnemonics": {}, "phrases": {}}
    with pg_engine.connect() as conn:
        for kind, sql in (
            ("meanings", "SELECT word_id, meaning_id FROM domain.meanings ORDER BY meaning_id"),
            ("mnemonics", "SELECT word_id, mnemonic_id FROM domain.mnemonics ORDER BY mnemonic_id"),
            ("phrases", "SELECT owner_word_id AS word_id, phrase_id FROM domain.phrases ORDER BY phrase_id"),
        ):
            for word_id, child_id in conn.execute(text(sql)):
                out[kind].setdefault(word_id, []).append(child_id)
    _log(f"    meanings map: {len(out['meanings'])} words; "
         f"mnemonics map: {len(out['mnemonics'])} words; "
         f"phrases map: {len(out['phrases'])} words")
    return out


def _load_sentence_map(pg_engine) -> dict[int, list[int]]:
    """meaning_id -> [sentence_id,...], for meaning table's sentences JSON col."""
    _log("  loading relation map (meaning -> sentences) ...")
    out: dict[int, list[int]] = {}
    with pg_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT meaning_id, sentence_id FROM domain.sentences ORDER BY sentence_id"
        ))
        for meaning_id, sentence_id in rows:
            out.setdefault(meaning_id, []).append(sentence_id)
    _log(f"    sentence map: {len(out)} meanings")
    return out


def _stream_and_insert(
    pg_engine,
    my_engine,
    *,
    table: str,
    select_sql: str,
    row_to_mysql,
    insert_sql: str,
    batch_size: int = 5000,
) -> int:
    """Stream rows from PG, map each, INSERT to MySQL shadow in batches."""
    _log(f"  loading {table} ...")
    total = 0
    batch: list[dict] = []
    with pg_engine.connect().execution_options(stream_results=True, yield_per=batch_size) as pg_conn:
        pg_rows = pg_conn.execute(text(select_sql))
        columns = pg_rows.keys()
        with my_engine.begin() as my_conn:
            for pg_row in pg_rows:
                mapped = row_to_mysql(dict(zip(columns, pg_row, strict=True)))
                batch.append(mapped)
                if len(batch) >= batch_size:
                    my_conn.execute(text(insert_sql), batch)
                    total += len(batch)
                    _log(f"    {table}: {total} rows inserted")
                    batch.clear()
            if batch:
                my_conn.execute(text(insert_sql), batch)
                total += len(batch)
    _log(f"  {table}: {total} total")
    return total


def _stage2_load_shadow(pg_engine, my_engine) -> dict[str, int]:
    _log("stage 2: PG -> MySQL shadow ...")
    counts: dict[str, int] = {}

    rel = _load_relations_map(pg_engine)
    sent_map = _load_sentence_map(pg_engine)

    word_cols_out = [
        "word_id", "type", "form", "phonetic_us", "audio_us", "phonetic_uk", "audio_uk",
        "meanings", "mnemonics", "plural", "phrases", "structure",
        "third_person", "present_participle", "past_tense", "past_participle",
        "base", "comparative", "superlative", "derivatives", "morpheme_derivatives",
        "family", "source", "status",
    ]
    def _w(pg_row: dict) -> dict:
        w = pg_row["word_id"]
        return row_to_mysql_word(
            pg_row,
            meaning_ids=rel["meanings"].get(w, []),
            mnemonic_ids=rel["mnemonics"].get(w, []),
            phrase_ids=rel["phrases"].get(w, []),
        )
    counts["word"] = _stream_and_insert(
        pg_engine, my_engine,
        table="word",
        select_sql=f"SELECT {_WORD_COLS} FROM domain.words ORDER BY word_id",
        row_to_mysql=_w,
        insert_sql=_insert_sql("word", word_cols_out),
    )

    meaning_cols_out = [
        "meaning_id", "word_id", "user_group", "pos", "pos_sub",
        "equivalents", "synonyms", "antonyms",
        "phonetic_us", "audio_us", "phonetic_uk", "audio_uk",
        "cn_paraphrase", "en_paraphrase", "sentences", "source",
    ]
    def _m(pg_row: dict) -> dict:
        return row_to_mysql_meaning(
            pg_row, sentence_ids=sent_map.get(pg_row["meaning_id"], []),
        )
    counts["meaning"] = _stream_and_insert(
        pg_engine, my_engine,
        table="meaning",
        select_sql=f"SELECT {_MEANING_COLS} FROM domain.meanings ORDER BY meaning_id",
        row_to_mysql=_m,
        insert_sql=_insert_sql("meaning", meaning_cols_out),
    )

    sentence_cols_out = [
        "sentence_id", "word_id", "meaning_id", "user_group",
        "form", "highlight", "translation",
        "audio_us", "audio_uk", "source", "citation", "citation_detail",
    ]
    counts["sentence"] = _stream_and_insert(
        pg_engine, my_engine,
        table="sentence",
        select_sql=_SENTENCE_SELECT,
        row_to_mysql=row_to_mysql_sentence,
        insert_sql=_insert_sql("sentence", sentence_cols_out),
    )

    mnemonic_cols_out = [
        "mnemonic_id", "word_id", "type", "user_group", "content", "source", "creator_id",
    ]
    counts["mnemonic"] = _stream_and_insert(
        pg_engine, my_engine,
        table="mnemonic",
        select_sql=f"SELECT {_MNEMONIC_COLS} FROM domain.mnemonics ORDER BY mnemonic_id",
        row_to_mysql=row_to_mysql_mnemonic,
        insert_sql=_insert_sql("mnemonic", mnemonic_cols_out),
    )

    phrase_cols_out = ["phrase_id", "form", "meaning", "audio_us", "audio_uk"]
    def _p(pg_row: dict) -> dict:
        return row_to_mysql_phrase(pg_row)
    counts["phrase"] = _stream_and_insert(
        pg_engine, my_engine,
        table="phrase",
        select_sql=f"SELECT {_PHRASE_COLS} FROM domain.phrases ORDER BY phrase_id",
        row_to_mysql=_p,
        insert_sql=_insert_sql("phrase", phrase_cols_out),
    )

    return counts


def _stage3_swap(my_engine) -> None:
    """Atomic shadow swap per spec §6 stage 3.

    Statement A: main -> _old, shadow -> main (single atomic RENAME).
    Statement B: _old -> _shadow (frees slot for next run).
    """
    _log("stage 3: atomic RENAME swap ...")
    stmt_a = ",".join(
        f" `{t}` TO `{t}_old`, `{t}_shadow` TO `{t}`"
        for t in TABLES
    )
    stmt_b = ",".join(
        f" `{t}_old` TO `{t}_shadow`"
        for t in TABLES
    )
    with my_engine.begin() as conn:
        conn.execute(text(f"RENAME TABLE{stmt_a}"))
        _log("  statement A committed (main+shadow swapped)")
        conn.execute(text(f"RENAME TABLE{stmt_b}"))
        _log("  statement B committed (_old -> _shadow)")


def _stage4_count_check(
    pg_engine, my_engine, counts: dict[str, int], *, dry_run: bool
) -> list[str]:
    """Compare PG domain.* vs MySQL main (post-swap).

    In dry-run we compare shadow instead of main, since swap didn't happen.
    """
    _log("stage 4: count check ...")
    mismatches: list[str] = []
    target_suffix = "_shadow" if dry_run else ""
    with pg_engine.connect() as pg_conn, my_engine.connect() as my_conn:
        for t in TABLES:
            pg_table = f"domain.{t}s" if t != "phrase" else "domain.phrases"
            pg_n = pg_conn.execute(text(f"SELECT count(*) FROM {pg_table}")).scalar_one()
            my_n = my_conn.execute(
                text(f"SELECT count(*) FROM `{t}{target_suffix}`")
            ).scalar_one()
            if pg_n != my_n:
                msg = f"{t}: PG={pg_n} MySQL={my_n} (loaded={counts.get(t)})"
                _log(f"  MISMATCH {msg}")
                mismatches.append(msg)
            else:
                _log(f"  OK {t}: {pg_n}")
    return mismatches


def _stage5_summary(
    counts: dict[str, int],
    mismatches: list[str],
    run_log: Path,
    *,
    dry_run: bool,
) -> None:
    _log(f"stage 5: summary counts={counts} mismatches={len(mismatches)}")
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dry_run": dry_run,
        "counts": counts,
        "mismatches": mismatches,
    }
    with run_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if mismatches:
        print(f"WARN drift detected: {mismatches}", file=sys.stderr, flush=True)


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
