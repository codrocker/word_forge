"""apply_web_changes: all-or-nothing PATCH writes with drift detection.

Per spec §3.4:
- Reuse reviewer.patch.check_drift + PatchDriftError
- DO NOT call reviewer.patch.apply_patch (array-index addressing, incompatible with target_id)
- First drift → raise; outer engine.begin() rolls back entire txn
- Each successful change writes one meta.edit_audit row in the same txn
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from wordforge.reviewer.patch import PatchDriftError, check_drift
from wordforge.web.services.audit_service import write_audit

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
