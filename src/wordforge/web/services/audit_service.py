"""Write meta.edit_audit rows inside caller's txn. Never opens its own."""
from __future__ import annotations

import json
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

_ALLOWED_OPS = {"update", "insert", "delete"}


def _to_jsonb(value: Any) -> str | None:
    """Serialize a Python value to a JSON string for JSONB columns."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def write_audit(
    conn: Connection,
    *,
    word_id: int,
    field_path: str,
    target_id: int | None,
    op: Literal["update", "insert", "delete"],
    old_value: Any,
    new_value: Any,
    editor_id: int,
) -> None:
    """Insert one audit row. Caller responsible for engine.begin() txn.

    target_id: None for `words.*` field changes; meaning_id / mnemonic_id / etc.
    for sub-table changes.
    """
    if op not in _ALLOWED_OPS:
        raise ValueError(f"invalid op: {op}")
    conn.execute(
        text(
            "INSERT INTO meta.edit_audit "
            "(word_id, field_path, target_id, op, old_value, new_value, editor_id) "
            "VALUES (:w, :fp, :tid, :op, CAST(:ov AS jsonb), CAST(:nv AS jsonb), :eid)"
        ),
        {
            "w": word_id,
            "fp": field_path,
            "tid": target_id,
            "op": op,
            "ov": _to_jsonb(old_value),
            "nv": _to_jsonb(new_value),
            "eid": editor_id,
        },
    )
