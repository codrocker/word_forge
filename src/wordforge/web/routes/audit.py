"""Audit log read route (M3.3)."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.engine import Engine

from wordforge.web.cursor import decode as decode_cursor, encode as encode_cursor
from wordforge.web.deps import current_editor, get_engine
from wordforge.web.errors import envelope_ok

router = APIRouter(prefix="/api/v1/audit", dependencies=[Depends(current_editor)])


@router.get("")
def list_audit(
    word_id: int | None = Query(None),
    editor_id: int | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    engine: Engine = Depends(get_engine),
):
    where: list[str] = []
    params: dict = {"lim": limit + 1}
    if word_id is not None:
        where.append("a.word_id = :w")
        params["w"] = word_id
    if editor_id is not None:
        where.append("a.editor_id = :ed")
        params["ed"] = editor_id
    if since is not None:
        where.append("a.created_at >= :since")
        params["since"] = since
    if until is not None:
        where.append("a.created_at <= :until")
        params["until"] = until
    if cursor:
        c = decode_cursor(cursor, "updated_at_desc")
        where.append("(a.created_at, a.id) < (:cu, :cid)")
        params["cu"] = c.u
        params["cid"] = c.w
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT a.id, a.word_id, a.field_path, a.target_id, a.op,
               a.old_value, a.new_value, a.created_at,
               e.id AS editor_id, e.display_name AS editor_display_name
          FROM meta.edit_audit a
          JOIN meta.editors e ON a.editor_id = e.id
          {where_sql}
         ORDER BY a.created_at DESC, a.id DESC
         LIMIT :lim
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    items = [
        {
            "id": r["id"],
            "word_id": r["word_id"],
            "field_path": r["field_path"],
            "target_id": r["target_id"],
            "op": r["op"],
            "old_value": r["old_value"],
            "new_value": r["new_value"],
            "editor": {"id": r["editor_id"], "display_name": r["editor_display_name"]},
            "created_at": r["created_at"],
        }
        for r in rows[:limit]
    ]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_cursor("updated_at_desc", last["created_at"].isoformat(), last["id"])
    return envelope_ok({"items": items, "next_cursor": next_cursor})
