"""CLI: export prod domain.* → flutter-friendly SQLite zip.

Usage:
    source ~/.wordforge/prod.env
    ./.venv/bin/python -m scripts.packaging.export_sailing_sqlite \\
        [--output PATH] [--limit N] [--dry-run]
"""

# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

# Support both `python -m scripts.packaging.export_sailing_sqlite` and
# direct `python scripts/packaging/export_sailing_sqlite.py` by ensuring
# the repo root is on sys.path before absolute imports resolve.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import sqlalchemy as sa  # noqa: E402

from scripts.packaging.builder import build_word_payload  # noqa: E402
from scripts.packaging.packager import write_sqlite, zip_db  # noqa: E402

_DEFAULT_OUTPUT = Path(
    "/Users/allen/code/jiyuan/frontent/sailing_words/assets/database/words.db.zip"
)

_log = logging.getLogger("packaging")


def _fetch_all(engine: sa.Engine, limit: int | None) -> tuple[
    list[dict],
    dict[int, list[dict]],
    dict[int, list[dict]],
    dict[int, list[dict]],
]:
    """Run the 4 SELECTs from spec §7.3. Aggregate by word_id / meaning_id."""
    words_sql = (
        "SELECT word_id, type, form, phonetic_us, phonetic_uk, audio_us, audio_uk "
        "FROM domain.words ORDER BY word_id"
    )
    if limit is not None:
        words_sql += f" LIMIT {int(limit)}"

    with engine.connect() as conn:
        _log.info("fetching domain.words ...")
        words = [dict(r._mapping) for r in conn.execute(sa.text(words_sql)).all()]
        word_ids = [w["word_id"] for w in words]
        _log.info("  got %d words", len(words))

        if not word_ids:
            return words, {}, {}, {}

        _log.info("fetching domain.meanings ...")
        meanings_by_wid: dict[int, list[dict]] = defaultdict(list)
        rows = conn.execute(
            sa.text(
                "SELECT meaning_id, word_id, pos, cn_paraphrase "
                "FROM domain.meanings WHERE word_id = ANY(:ids) "
                "ORDER BY word_id, meaning_id"
            ),
            {"ids": word_ids},
        ).all()
        for r in rows:
            meanings_by_wid[r.word_id].append(dict(r._mapping))
        meaning_ids = [
            m["meaning_id"] for ms in meanings_by_wid.values() for m in ms
        ]
        _log.info(
            "  got %d meanings across %d words", len(rows), len(meanings_by_wid)
        )

        _log.info("fetching domain.sentences ...")
        sentences_by_mid: dict[int, list[dict]] = defaultdict(list)
        if meaning_ids:
            rows = conn.execute(
                sa.text(
                    "SELECT sentence_id, meaning_id, form, translation "
                    "FROM domain.sentences WHERE meaning_id = ANY(:ids) "
                    "ORDER BY meaning_id, sentence_id"
                ),
                {"ids": meaning_ids},
            ).all()
            for r in rows:
                sentences_by_mid[r.meaning_id].append(dict(r._mapping))
            _log.info("  got %d sentences", len(rows))

        _log.info("fetching domain.mnemonics ...")
        mnemonics_by_wid: dict[int, list[dict]] = defaultdict(list)
        rows = conn.execute(
            sa.text(
                "SELECT mnemonic_id, word_id, type, content "
                "FROM domain.mnemonics WHERE word_id = ANY(:ids) "
                "ORDER BY word_id, mnemonic_id"
            ),
            {"ids": word_ids},
        ).all()
        for r in rows:
            mnemonics_by_wid[r.word_id].append(dict(r._mapping))
        _log.info("  got %d mnemonics", len(rows))

    return words, dict(meanings_by_wid), dict(sentences_by_mid), dict(mnemonics_by_wid)


def _build_all(words, meanings_by_wid, sentences_by_mid, mnemonics_by_wid):
    for w in words:
        wid = w["word_id"]
        payload = build_word_payload(
            w,
            meanings=meanings_by_wid.get(wid, []),
            sentences_by_mid=sentences_by_mid,
            mnemonics=mnemonics_by_wid.get(wid, []),
        )
        yield wid, json.dumps(payload, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    p = argparse.ArgumentParser(
        description=__doc__ and __doc__.splitlines()[0]
    )
    p.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    p.add_argument("--limit", type=int, default=None, help="debug: only first N words")
    p.add_argument(
        "--dry-run", action="store_true", help="build JSON only, do not write files"
    )
    args = p.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set (did you `source ~/.wordforge/prod.env`?)")

    t0 = time.perf_counter()
    engine = sa.create_engine(url, future=True)
    try:
        words, meanings_by_wid, sentences_by_mid, mnemonics_by_wid = _fetch_all(
            engine, args.limit
        )
    finally:
        engine.dispose()

    rows = list(
        _build_all(words, meanings_by_wid, sentences_by_mid, mnemonics_by_wid)
    )
    _log.info("built %d word payloads", len(rows))

    if args.dry_run:
        _log.info(
            "--dry-run: skip sqlite + zip. total time=%.1fs",
            time.perf_counter() - t0,
        )
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "words.db"
        n = write_sqlite(db_path, rows)
        db_size_mb = db_path.stat().st_size / 1_000_000
        _log.info("sqlite written: %d rows, %.1f MB", n, db_size_mb)
        zip_db(db_path, args.output)

    zip_size_mb = args.output.stat().st_size / 1_000_000
    _log.info("zip written: %s (%.1f MB)", args.output, zip_size_mb)
    _log.info("total time=%.1fs", time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
