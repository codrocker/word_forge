"""apply_web_changes: all-or-nothing PATCH writes with drift detection.

Per spec §3.4:
- Reuse reviewer.patch.check_drift + PatchDriftError
- DO NOT call reviewer.patch.apply_patch (array-index addressing, incompatible with target_id)
- First drift → raise; outer engine.begin() rolls back entire txn
- Each successful change writes one meta.edit_audit row in the same txn
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from wordforge.reviewer.patch import PatchDriftError, check_drift
from wordforge.web.services.audit_service import write_audit

HUMAN_WEB = "human:web"

# field_path → (table, column, has_updated_at)
# Only listed fields are editable via PATCH.
FIELD_MAP: dict[str, tuple[str, str, bool]] = {
    # domain.words: has updated_at
    "words.form": ("domain.words", "form", True),
    "words.phonetic_us": ("domain.words", "phonetic_us", True),
    "words.phonetic_uk": ("domain.words", "phonetic_uk", True),
    "words.status": ("domain.words", "status", True),
    "words.quality_flag": ("domain.words", "quality_flag", True),
    # domain.meanings / mnemonics / sentences: NO updated_at (CLAUDE.md hard rule)
    "meanings.cn_paraphrase": ("domain.meanings", "cn_paraphrase", False),
    "meanings.en_paraphrase": ("domain.meanings", "en_paraphrase", False),
    "mnemonics.content": ("domain.mnemonics", "content", False),
    "sentences.form": ("domain.sentences", "form", False),
    "sentences.translation": ("domain.sentences", "translation", False),
}

# table → primary key column
_PK_COL: dict[str, str] = {
    "domain.words": "word_id",
    "domain.meanings": "meaning_id",
    "domain.mnemonics": "mnemonic_id",
    "domain.sentences": "sentence_id",
}


def apply_web_changes(
    conn: Connection,
    *,
    word_id: int,
    changes: list[dict[str, Any]],
    editor_id: int,
) -> int:
    """Apply changes in order; first drift raises. Caller owns engine.begin()."""
    applied = 0
    for ch in changes:
        op = ch["op"]
        if op != "update":
            raise NotImplementedError("M4.2 covers op=update only")
        fp: str = ch["field_path"]
        if fp not in FIELD_MAP:
            raise ValueError(f"unknown field_path: {fp}")
        table, column, has_ts = FIELD_MAP[fp]
        pk_col = _PK_COL[table]
        target_id = ch.get("target_id")
        # domain.words: PK = word_id; sub-tables: PK = target_id
        pk_val = word_id if table == "domain.words" else target_id
        if pk_val is None:
            raise ValueError(f"target_id required for {fp}")

        cur = conn.execute(
            text(f"SELECT {column} AS v FROM {table} WHERE {pk_col} = :pk"),
            {"pk": pk_val},
        ).first()
        if cur is None:
            raise ValueError(f"{table} pk={pk_val} not found")
        # check_drift raises PatchDriftError on mismatch
        check_drift(path=fp, current=cur.v, old=ch["old_value"])

        ts_clause = ", updated_at = now()" if has_ts else ""
        conn.execute(
            text(f"UPDATE {table} SET {column} = :v{ts_clause} WHERE {pk_col} = :pk"),
            {"v": ch["new_value"], "pk": pk_val},
        )
        # audit: for domain.words changes, target_id is NULL
        audit_target = None if table == "domain.words" else target_id
        write_audit(
            conn,
            word_id=word_id,
            field_path=fp,
            target_id=audit_target,
            op="update",
            old_value=ch["old_value"],
            new_value=ch["new_value"],
            editor_id=editor_id,
        )
        applied += 1
    return applied


def create_web_word(conn: Connection, *, body: dict[str, Any], editor_id: int) -> tuple[int, bool]:
    """Create a word with sub-tables. Return (word_id, created).

    created=False when form+type already exists (UNIQUE conflict).
    All sub-table source fields are forced to 'human:web'.
    """
    form = body["form"].strip()
    type_ = body["type"]

    # --- Check existing ---
    existing = conn.execute(
        text("SELECT word_id FROM domain.words WHERE form = :f AND type = :t"),
        {"f": form, "t": type_},
    ).first()
    if existing is not None:
        return existing.word_id, False

    # --- Insert word ---
    try:
        row = conn.execute(
            text(
                "INSERT INTO domain.words (type, form, phonetic_us, phonetic_uk, source, status, quality_flag) "
                "VALUES (:t, :f, :pu, :pk, :src, 0, 'none') RETURNING word_id"
            ),
            {
                "t": type_,
                "f": form,
                "pu": body.get("phonetic_us"),
                "pk": body.get("phonetic_uk"),
                "src": HUMAN_WEB,
            },
        ).first()
    except IntegrityError:
        # Concurrent insert won the race
        conn.rollback()
        existing = conn.execute(
            text("SELECT word_id FROM domain.words WHERE form = :f AND type = :t"),
            {"f": form, "t": type_},
        ).first()
        return existing.word_id, False

    word_id = row.word_id
    write_audit(
        conn,
        word_id=word_id,
        field_path="words",
        target_id=None,
        op="insert",
        old_value=None,
        new_value={"form": form, "type": type_},
        editor_id=editor_id,
    )

    # --- Meanings ---
    meaning_ids: list[int] = []
    for m in body.get("meanings") or []:
        mrow = conn.execute(
            text(
                "INSERT INTO domain.meanings "
                "(word_id, pos, pos_sub, cn_paraphrase, en_paraphrase, source) "
                "VALUES (:w, :p, :ps, :cn, :en, :src) RETURNING meaning_id"
            ),
            {
                "w": word_id,
                "p": m.get("pos"),
                "ps": m.get("pos_sub"),
                "cn": m.get("cn_paraphrase"),
                "en": m.get("en_paraphrase"),
                "src": HUMAN_WEB,
            },
        ).first()
        meaning_ids.append(mrow.meaning_id)
        write_audit(
            conn,
            word_id=word_id,
            field_path="meanings",
            target_id=mrow.meaning_id,
            op="insert",
            old_value=None,
            new_value=m,
            editor_id=editor_id,
        )

    # --- Sentences (meaning_index → meaning_id) ---
    for s in body.get("sentences") or []:
        mid = meaning_ids[s["meaning_index"]]
        srow = conn.execute(
            text(
                "INSERT INTO domain.sentences "
                "(meaning_id, form, translation, highlight, source) "
                "VALUES (:m, :f, :t, :h, :src) RETURNING sentence_id"
            ),
            {
                "m": mid,
                "f": s["form"],
                "t": s["translation"],
                "h": json.dumps(s["highlight"], ensure_ascii=False) if s.get("highlight") else None,
                "src": HUMAN_WEB,
            },
        ).first()
        write_audit(
            conn,
            word_id=word_id,
            field_path="sentences",
            target_id=srow.sentence_id,
            op="insert",
            old_value=None,
            new_value=s,
            editor_id=editor_id,
        )

    # --- Mnemonics ---
    for mn in body.get("mnemonics") or []:
        mnrow = conn.execute(
            text(
                "INSERT INTO domain.mnemonics (word_id, type, content, source) "
                "VALUES (:w, :t, :c, :src) RETURNING mnemonic_id"
            ),
            {
                "w": word_id,
                "t": mn.get("type", 1),
                "c": json.dumps(mn["content"], ensure_ascii=False),
                "src": HUMAN_WEB,
            },
        ).first()
        write_audit(
            conn,
            word_id=word_id,
            field_path="mnemonics",
            target_id=mnrow.mnemonic_id,
            op="insert",
            old_value=None,
            new_value=mn,
            editor_id=editor_id,
        )

    # --- Phrases (owner_word_id, not word_id) ---
    for ph in body.get("phrases") or []:
        prow = conn.execute(
            text(
                "INSERT INTO domain.phrases (owner_word_id, form, meaning, source) "
                "VALUES (:w, :f, :m, :src) RETURNING phrase_id"
            ),
            {
                "w": word_id,
                "f": ph["form"],
                "m": ph.get("meaning"),
                "src": HUMAN_WEB,
            },
        ).first()
        write_audit(
            conn,
            word_id=word_id,
            field_path="phrases",
            target_id=prow.phrase_id,
            op="insert",
            old_value=None,
            new_value=ph,
            editor_id=editor_id,
        )

    return word_id, True
