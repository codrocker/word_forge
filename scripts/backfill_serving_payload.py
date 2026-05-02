"""Backfill serving.word_payload for every word in domain.words.

Rationale: migration 0009 adds the table empty. All 121k words already
have data in domain.* (from previous export runs). Re-running the full
ExportStage to populate serving.* would re-execute every preflight
check and cost more DB I/O than needed — since serving.word_payload
is pure SELECT FROM domain.*, we can build it directly.

Idempotent: ON CONFLICT (word_id) DO UPDATE. Safe to re-run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# ruff: noqa: E501


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def backfill(batch_size: int = 5000) -> int:
    import sqlalchemy as sa

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set")

    engine = sa.create_engine(url, future=True)
    try:
        # Grab every word_id, iterate per-word. SELECT + JSON build + upsert.
        with engine.connect() as conn:
            total = conn.execute(sa.text("SELECT COUNT(*) FROM domain.words")).scalar()
        _log(f"backfilling serving.word_payload for {total} words")

        with engine.connect() as ro:
            word_ids = [r[0] for r in ro.execute(
                sa.text("SELECT word_id FROM domain.words ORDER BY word_id")
            ).all()]

        done = 0
        batch: list[dict] = []
        t0 = time.perf_counter()
        with engine.begin() as conn:
            for wid in word_ids:
                payload = _build_payload(conn, wid)
                if payload is None:
                    continue
                batch.append({
                    "wid": wid, "form": payload["form"], "type": payload["type"],
                    "payload": json.dumps(payload, ensure_ascii=False),
                })
                if len(batch) >= batch_size:
                    _flush(conn, batch)
                    done += len(batch)
                    batch.clear()
                    rate = done / (time.perf_counter() - t0) if done else 0
                    _log(f"  streamed {done}/{total} ({rate:.0f} w/s)")
            if batch:
                _flush(conn, batch)
                done += len(batch)
        _log(f"done: {done} serving.word_payload rows upserted in "
             f"{time.perf_counter()-t0:.1f}s")
    finally:
        engine.dispose()
    return 0


def _flush(conn, batch):
    import sqlalchemy as sa
    conn.execute(
        sa.text(
            "INSERT INTO serving.word_payload "
            "(word_id, form, type, payload, payload_schema_version, updated_at) "
            "VALUES (:wid, :form, :type, CAST(:payload AS jsonb), 1, now()) "
            "ON CONFLICT (word_id) DO UPDATE SET "
            "  form = EXCLUDED.form, "
            "  type = EXCLUDED.type, "
            "  payload = EXCLUDED.payload, "
            "  payload_schema_version = EXCLUDED.payload_schema_version, "
            "  updated_at = now()"
        ),
        batch,
    )


def _build_payload(conn, word_id: int):
    import sqlalchemy as sa
    row = conn.execute(
        sa.text(
            "SELECT form, type, phonetic_us, phonetic_uk, audio_us, audio_uk "
            "FROM domain.words WHERE word_id = :w"
        ),
        {"w": word_id},
    ).first()
    if row is None:
        return None
    form, type_, ph_us, ph_uk, a_us, a_uk = row

    meanings = conn.execute(
        sa.text(
            "SELECT meaning_id, pos, pos_sub, cn_paraphrase, en_paraphrase, "
            "equivalents, synonyms, antonyms "
            "FROM domain.meanings WHERE word_id = :w ORDER BY meaning_id"
        ),
        {"w": word_id},
    ).all()
    meaning_blocks = []
    meaning_ids = [m[0] for m in meanings]
    sentences_map: dict = {}
    if meaning_ids:
        srows = conn.execute(
            sa.text(
                "SELECT meaning_id, sentence_id, form AS en, translation AS cn "
                "FROM domain.sentences WHERE meaning_id = ANY(:mids) "
                "ORDER BY meaning_id, sentence_id"
            ),
            {"mids": meaning_ids},
        ).all()
        for mid, sid, en, cn in srows:
            sentences_map.setdefault(mid, []).append(
                {"sentence_id": sid, "en": en, "cn": cn}
            )
    for m in meanings:
        meaning_blocks.append({
            "meaning_id": m[0], "pos": m[1], "pos_sub": m[2],
            "cn": m[3], "en": m[4],
            "equivalents": m[5], "synonyms": m[6], "antonyms": m[7],
            "sentences": sentences_map.get(m[0], []),
        })

    mnem = conn.execute(
        sa.text(
            "SELECT content, type FROM domain.mnemonics "
            "WHERE word_id = :w ORDER BY mnemonic_id LIMIT 1"
        ),
        {"w": word_id},
    ).first()
    mnemonic = {"content": mnem[0], "type": mnem[1]} if mnem else None

    phrase_rows = conn.execute(
        sa.text(
            "SELECT phrase_id, form, meaning "
            "FROM domain.phrases WHERE owner_word_id = :w ORDER BY phrase_id"
        ),
        {"w": word_id},
    ).all()
    phrases = [
        {"phrase_id": p[0], "en": p[1], "meaning": p[2]} for p in phrase_rows
    ]

    pkg_rows = conn.execute(
        sa.text(
            "SELECT package_id, unit_id, sort_order, importance "
            "FROM domain.package_word WHERE word_id = :w "
            "ORDER BY package_id, sort_order"
        ),
        {"w": word_id},
    ).all()
    packages = [
        {
            "package_id": p[0], "unit_id": p[1],
            "sort_order": float(p[2]), "importance": p[3],
        } for p in pkg_rows
    ]

    return {
        "form": form, "type": type_,
        "phonetic": {"us": ph_us, "uk": ph_uk, "audio_us": a_us, "audio_uk": a_uk},
        "meanings": meaning_blocks,
        "mnemonic": mnemonic,
        "phrases": phrases,
        "packages": packages,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--batch-size", type=int, default=5000)
    args = p.parse_args()
    return backfill(args.batch_size)


if __name__ == "__main__":
    sys.exit(main())
