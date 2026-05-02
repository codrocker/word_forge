"""JSON-patch application to app.* schema.

Accepts patches produced by the opus fixer (see prompts.OPUS_FIXER):
  - op=update  with meanings[i].cn_paraphrase / .en_paraphrase /
               .examples[j].en / .examples[j].cn / mnemonic.text
  - op=delete  with meanings[i] or meanings[i].examples[j]

Enforces old_value drift check — if the current DB value doesn't match
the patch's old_value, we raise PatchDriftError so the caller can
record-but-skip that specific patch (the rest of the word's patches
still apply in the enclosing txn).

Single-txn semantics live in _apply_patches_for_word: read meaning_ids
once, apply all patches in order. Caller decides drift policy.
"""

from __future__ import annotations

import json
import re
from typing import Any

import sqlalchemy as sa

_PATH_M_CN = re.compile(r"^meanings\[(\d+)\]\.cn_paraphrase$")
_PATH_M_EN = re.compile(r"^meanings\[(\d+)\]\.en_paraphrase$")
_PATH_M_EX_EN = re.compile(r"^meanings\[(\d+)\]\.examples\[(\d+)\]\.en$")
_PATH_M_EX_CN = re.compile(r"^meanings\[(\d+)\]\.examples\[(\d+)\]\.cn$")
_PATH_M_DEL = re.compile(r"^meanings\[(\d+)\]$")
_PATH_EX_DEL = re.compile(r"^meanings\[(\d+)\]\.examples\[(\d+)\]$")
_PATH_MN_TEXT = re.compile(r"^mnemonic\.text$")


class PatchDriftError(RuntimeError):
    """Raised when old_value in the LLM patch no longer matches the DB row.

    Means someone (or another worker) modified this column between blob
    build and apply. The enclosing _apply_patches_for_word logs this and
    skips the single patch, letting sibling patches for the same word
    still run.
    """


def check_drift(path: str, current: Any, old: Any) -> None:
    """Raise PatchDriftError if old_value disagrees with current DB value.

    `None` old_value == caller opted out; accepted silently. Everything
    else compared as .strip()'d str for robustness against JSONB quoting
    round-trips.
    """
    if old is None:
        return
    if current is None or str(current).strip() != str(old).strip():
        raise PatchDriftError(
            f"old_value drift at {path!r}: db={current!r} patch_old={old!r}"
        )


def apply_patch(conn, word_id: int, meaning_ids: list[int], patch: dict) -> bool:
    """Execute one patch. Raise on schema drift OR old_value drift.

    Returns True iff a row was actually modified.
    """
    op = patch.get("op", "update")
    path = patch.get("path", "")

    if op == "update":
        new = patch.get("new_value")
        old = patch.get("old_value")
        if (m := _PATH_M_CN.match(path)):
            i = int(m.group(1))
            if i >= len(meaning_ids):
                return False
            cur = conn.execute(
                sa.text("SELECT cn_paraphrase FROM domain.meanings WHERE meaning_id=:mid"),
                {"mid": meaning_ids[i]},
            ).scalar()
            check_drift(path, cur, old)
            return conn.execute(
                sa.text("UPDATE domain.meanings SET cn_paraphrase=:v WHERE meaning_id=:mid"),
                {"v": new, "mid": meaning_ids[i]},
            ).rowcount > 0
        if (m := _PATH_M_EN.match(path)):
            i = int(m.group(1))
            if i >= len(meaning_ids):
                return False
            cur = conn.execute(
                sa.text("SELECT en_paraphrase FROM domain.meanings WHERE meaning_id=:mid"),
                {"mid": meaning_ids[i]},
            ).scalar()
            check_drift(path, cur, old)
            return conn.execute(
                sa.text("UPDATE domain.meanings SET en_paraphrase=:v WHERE meaning_id=:mid"),
                {"v": new, "mid": meaning_ids[i]},
            ).rowcount > 0
        if (m := _PATH_M_EX_EN.match(path)) or (m := _PATH_M_EX_CN.match(path)):
            i, j = int(m.group(1)), int(m.group(2))
            if i >= len(meaning_ids):
                return False
            sents = conn.execute(
                sa.text(
                    "SELECT sentence_id, form, translation FROM domain.sentences "
                    "WHERE meaning_id=:mid ORDER BY sentence_id"
                ),
                {"mid": meaning_ids[i]},
            ).all()
            if j >= len(sents):
                return False
            is_en = bool(_PATH_M_EX_EN.match(path))
            col = "form" if is_en else "translation"
            cur = sents[j][1] if is_en else sents[j][2]
            check_drift(path, cur, old)
            return conn.execute(
                sa.text(f"UPDATE domain.sentences SET {col}=:v WHERE sentence_id=:sid"),
                {"v": new, "sid": sents[j][0]},
            ).rowcount > 0
        if _PATH_MN_TEXT.match(path):
            row = conn.execute(
                sa.text("SELECT mnemonic_id, content FROM domain.mnemonics WHERE word_id=:w"),
                {"w": word_id},
            ).first()
            if not row:
                return False
            mid, content = row
            content_dict = content if isinstance(content, dict) else json.loads(content)
            check_drift(path, content_dict.get("text"), old)
            content_dict["text"] = new
            return conn.execute(
                sa.text(
                    "UPDATE domain.mnemonics SET content=CAST(:v AS jsonb) "
                    "WHERE mnemonic_id=:mid"
                ),
                {"v": json.dumps(content_dict, ensure_ascii=False), "mid": mid},
            ).rowcount > 0
        return False

    if op == "delete":
        if (m := _PATH_M_DEL.match(path)):
            i = int(m.group(1))
            if i >= len(meaning_ids):
                return False
            # sentences CASCADE via FK; no manual delete needed.
            return conn.execute(
                sa.text("DELETE FROM domain.meanings WHERE meaning_id=:mid"),
                {"mid": meaning_ids[i]},
            ).rowcount > 0
        if (m := _PATH_EX_DEL.match(path)):
            i, j = int(m.group(1)), int(m.group(2))
            if i >= len(meaning_ids):
                return False
            sents = conn.execute(
                sa.text(
                    "SELECT sentence_id FROM domain.sentences "
                    "WHERE meaning_id=:mid ORDER BY sentence_id"
                ),
                {"mid": meaning_ids[i]},
            ).all()
            if j >= len(sents):
                return False
            return conn.execute(
                sa.text("DELETE FROM domain.sentences WHERE sentence_id=:sid"),
                {"sid": sents[j][0]},
            ).rowcount > 0
        return False

    return False


def apply_patches_for_word(
    engine, word_id: int, patches: list[dict]
) -> tuple[int, list[dict]]:
    """Single txn: read meaning_ids + apply all patches.

    PatchDriftError (LLM's `old_value` doesn't match current DB) is
    recorded and the specific patch is skipped — the rest of the word's
    patches still apply in the same txn. Any other exception propagates
    (fail-loud) and rolls the txn back.

    Returns (applied_count, skipped_list). skipped_list entries:
      {"path": ..., "op": ..., "reason": "drift", "detail": str(exc)}
    """
    applied = 0
    skipped: list[dict] = []
    with engine.begin() as conn:
        meaning_ids = [
            r[0]
            for r in conn.execute(
                sa.text(
                    "SELECT meaning_id FROM domain.meanings "
                    "WHERE word_id=:w ORDER BY meaning_id"
                ),
                {"w": word_id},
            ).all()
        ]
        for patch in patches:
            try:
                if apply_patch(conn, word_id, meaning_ids, patch):
                    applied += 1
            except PatchDriftError as e:
                skipped.append({
                    "path": patch.get("path"),
                    "op": patch.get("op"),
                    "reason": "drift",
                    "detail": str(e),
                })
    return applied, skipped
