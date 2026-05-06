"""Serving read-model rebuild: domain.* → serving.word_payload JSONB."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import sqlalchemy as sa

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def rebuild_word_payload(conn: Connection, word_id: int) -> None:
    """Rebuild serving.word_payload for a single word.

    Behaviour:
      - status == 1  → UPSERT aggregated JSONB payload
      - status in (0, 2) or word row missing → DELETE (remove from serving)

    Runs on *caller's* connection — does NOT open its own transaction.
    Caller is responsible for wrapping in ``engine.begin()``.
    """
    row = conn.execute(
        sa.text(
            "SELECT form, type, phonetic_us, phonetic_uk, audio_us, audio_uk, "
            "status, quality_flag "
            "FROM domain.words WHERE word_id = :w"
        ),
        {"w": word_id},
    ).first()

    # Word missing (hard-deleted) or non-published status → purge serving row
    if row is None or row.status != 1:
        conn.execute(
            sa.text("DELETE FROM serving.word_payload WHERE word_id = :w"),
            {"w": word_id},
        )
        return

    form = row.form
    type_ = row.type
    status = row.status
    quality_flag = row.quality_flag or "none"

    # --- Aggregate child data ---

    meanings = conn.execute(
        sa.text(
            "SELECT meaning_id, pos, pos_sub, cn_paraphrase, en_paraphrase, "
            "equivalents, synonyms, antonyms "
            "FROM domain.meanings WHERE word_id = :w ORDER BY meaning_id"
        ),
        {"w": word_id},
    ).all()

    meaning_blocks = []
    for m in meanings:
        sentences = conn.execute(
            sa.text(
                "SELECT sentence_id, form AS en, translation AS cn "
                "FROM domain.sentences WHERE meaning_id = :mid ORDER BY sentence_id"
            ),
            {"mid": m.meaning_id},
        ).all()
        meaning_blocks.append({
            "meaning_id": m.meaning_id,
            "pos": m.pos,
            "pos_sub": m.pos_sub,
            "cn": m.cn_paraphrase,
            "en": m.en_paraphrase,
            "equivalents": m.equivalents,
            "synonyms": m.synonyms,
            "antonyms": m.antonyms,
            "sentences": [
                {"sentence_id": s.sentence_id, "en": s.en, "cn": s.cn}
                for s in sentences
            ],
        })

    mnemonic_row = conn.execute(
        sa.text(
            "SELECT content, type FROM domain.mnemonics "
            "WHERE word_id = :w ORDER BY mnemonic_id LIMIT 1"
        ),
        {"w": word_id},
    ).first()
    mnemonic = (
        {"content": mnemonic_row.content, "type": mnemonic_row.type}
        if mnemonic_row
        else None
    )

    phrase_rows = conn.execute(
        sa.text(
            "SELECT phrase_id, form, meaning "
            "FROM domain.phrases WHERE owner_word_id = :w ORDER BY phrase_id"
        ),
        {"w": word_id},
    ).all()
    phrases = [
        {"phrase_id": p.phrase_id, "en": p.form, "meaning": p.meaning}
        for p in phrase_rows
    ]

    package_rows = conn.execute(
        sa.text(
            "SELECT package_id, unit_id, sort_order, importance "
            "FROM domain.package_word WHERE word_id = :w "
            "ORDER BY package_id, sort_order"
        ),
        {"w": word_id},
    ).all()
    packages = [
        {
            "package_id": p.package_id,
            "unit_id": p.unit_id,
            "sort_order": float(p.sort_order),
            "importance": p.importance,
        }
        for p in package_rows
    ]

    # --- Build payload with status + quality_flag ---

    payload = {
        "form": form,
        "type": type_,
        "status": status,
        "quality_flag": quality_flag,
        "phonetic": {
            "us": row.phonetic_us,
            "uk": row.phonetic_uk,
            "audio_us": row.audio_us,
            "audio_uk": row.audio_uk,
        },
        "meanings": meaning_blocks,
        "mnemonic": mnemonic,
        "phrases": phrases,
        "packages": packages,
    }

    # --- UPSERT ---

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
        {
            "wid": word_id,
            "form": form,
            "type": type_,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )
