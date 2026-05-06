"""Words routes: search + detail (M3) + patch (M4.3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.engine import Engine

from wordforge.db.serving import rebuild_word_payload
from wordforge.web.cursor import decode as decode_cursor, encode as encode_cursor
from wordforge.web.deps import current_editor, get_engine
from wordforge.web.errors import envelope_ok
from wordforge.web.schemas.words import PatchRequest, QualityChangeRequest, StatusChangeRequest
from wordforge.web.services.word_service import apply_web_changes

router = APIRouter(prefix="/api/v1/words", dependencies=[Depends(current_editor)])


@router.get("")
def search(
    q: str | None = Query(None, max_length=100),
    status_: int | None = Query(None, alias="status", ge=0, le=2),
    quality: str | None = Query(None, pattern="^(none|suspect|fixed)$"),
    type_: int | None = Query(None, alias="type", ge=1, le=2),
    pos: int | None = Query(None),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    engine: Engine = Depends(get_engine),
):
    where: list[str] = []
    params: dict = {"lim": limit + 1}
    if q:
        where.append("w.form ILIKE :q")
        params["q"] = f"%{q}%"
    if status_ is not None:
        where.append("w.status = :s")
        params["s"] = status_
    if quality:
        where.append("w.quality_flag = :qf")
        params["qf"] = quality
    if type_ is not None:
        where.append("w.type = :tp")
        params["tp"] = type_
    if pos is not None:
        where.append(
            "EXISTS (SELECT 1 FROM domain.meanings m "
            "WHERE m.word_id = w.word_id AND m.pos = :pos)"
        )
        params["pos"] = pos
    if cursor:
        c = decode_cursor(cursor, "updated_at_desc")
        where.append("(w.updated_at, w.word_id) < (:cu, :cw)")
        params["cu"] = c.u
        params["cw"] = c.w
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT w.word_id, w.form, w.type, w.status, w.quality_flag, w.updated_at,
               (SELECT COUNT(*) FROM domain.meanings m WHERE m.word_id = w.word_id) AS meaning_count
          FROM domain.words w
          {where_sql}
         ORDER BY w.updated_at DESC, w.word_id DESC
         LIMIT :lim
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    items = [dict(r) for r in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        next_cursor = encode_cursor(
            "updated_at_desc", last["updated_at"].isoformat(), last["word_id"]
        )
    return envelope_ok({"items": items, "next_cursor": next_cursor})


@router.get("/{word_id}")
def detail(word_id: int, engine: Engine = Depends(get_engine)):
    with engine.connect() as conn:
        word = conn.execute(
            text("SELECT * FROM domain.words WHERE word_id = :w"),
            {"w": word_id},
        ).mappings().first()
        if word is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="word not found"
            )
        meanings = conn.execute(
            text(
                "SELECT * FROM domain.meanings WHERE word_id = :w ORDER BY meaning_id"
            ),
            {"w": word_id},
        ).mappings().all()
        mnemonics = conn.execute(
            text(
                "SELECT * FROM domain.mnemonics WHERE word_id = :w ORDER BY mnemonic_id"
            ),
            {"w": word_id},
        ).mappings().all()
        # domain.sentences has no word_id — must JOIN meanings
        sentences = conn.execute(
            text(
                "SELECT s.* FROM domain.sentences s "
                "JOIN domain.meanings m ON s.meaning_id = m.meaning_id "
                "WHERE m.word_id = :w ORDER BY s.sentence_id"
            ),
            {"w": word_id},
        ).mappings().all()
        phrases = conn.execute(
            text(
                "SELECT * FROM domain.phrases WHERE word_id = :w ORDER BY phrase_id"
            ),
            {"w": word_id},
        ).mappings().all()
    return envelope_ok(
        {
            "word": dict(word),
            "meanings": [dict(m) for m in meanings],
            "mnemonics": [dict(m) for m in mnemonics],
            "sentences": [dict(s) for s in sentences],
            "phrases": [dict(p) for p in phrases],
        }
    )


@router.patch("/{word_id}")
def patch_word(
    word_id: int,
    body: PatchRequest,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM domain.words WHERE word_id = :w"),
            {"w": word_id},
        ).first()
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="word not found"
            )
        applied = apply_web_changes(
            conn,
            word_id=word_id,
            editor_id=editor["id"],
            changes=[c.model_dump() for c in body.changes],
        )
        rebuild_word_payload(conn, word_id)
    return envelope_ok({"applied": applied})


@router.post("/{word_id}/status")
def change_status(
    word_id: int,
    body: StatusChangeRequest,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM domain.words WHERE word_id = :w"),
            {"w": word_id},
        ).first()
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="word not found"
            )
        apply_web_changes(
            conn,
            word_id=word_id,
            editor_id=editor["id"],
            changes=[
                {
                    "field_path": "words.status",
                    "target_id": None,
                    "op": "update",
                    "old_value": body.old_value,
                    "new_value": body.new_value,
                }
            ],
        )
        rebuild_word_payload(conn, word_id)
    return envelope_ok(None)


@router.post("/{word_id}/quality")
def change_quality(
    word_id: int,
    body: QualityChangeRequest,
    editor: dict = Depends(current_editor),
    engine: Engine = Depends(get_engine),
):
    with engine.begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM domain.words WHERE word_id = :w"),
            {"w": word_id},
        ).first()
        if exists is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="word not found"
            )
        apply_web_changes(
            conn,
            word_id=word_id,
            editor_id=editor["id"],
            changes=[
                {
                    "field_path": "words.quality_flag",
                    "target_id": None,
                    "op": "update",
                    "old_value": body.old_value,
                    "new_value": body.new_value,
                }
            ],
        )
        rebuild_word_payload(conn, word_id)
    return envelope_ok(None)
