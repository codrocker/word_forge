"""Build a single word 'blob' from app.* tables — one dict suitable for
sending to an LLM as JSON. Read-only against the database; no writes.

Shape:
  {
    "word_id": int, "form": str, "type": int, "phonetic_us": str, ...,
    "meanings": [
      {"meaning_id": int, "pos": int, "pos_name": str,
       "cn_paraphrase": str, "en_paraphrase": str (<=CFG chars),
       "examples": [{"en": ..., "cn": ...}, ...]},
      ...
    ],
    "mnemonic": {"kind": "phonetic", "text": str, ...}?  # optional
  }

Long en_paraphrase is truncated to CFG.EN_PARAPHRASE_CHAR_LIMIT to keep
prompt sizes bounded (paraphrases >180 chars are usually Collins boilerplate
we're about to flag anyway).
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from wordforge.reviewer.config import CFG

POS_NAME: dict[int, str] = {
    1: "noun", 2: "verb", 3: "adj", 4: "number", 5: "pronoun",
    6: "adverb", 7: "article", 8: "preposition", 9: "conjunction",
    10: "interjection", 201: "phrasal verb",
}


def build_word_blob(
    engine, form: str | None, word_id: int | None
) -> dict[str, Any] | None:
    """Return the word's full blob, or None if the word doesn't exist.

    Exactly one of (form, word_id) should be provided; word_id is preferred
    when available (unique), form picks the smallest word_id on ties.
    """
    with engine.connect() as conn:
        if word_id is not None:
            w_row = conn.execute(
                sa.text("SELECT * FROM domain.words WHERE word_id = :w"),
                {"w": word_id},
            ).mappings().first()
        else:
            w_row = conn.execute(
                sa.text("SELECT * FROM domain.words WHERE form = :f ORDER BY word_id LIMIT 1"),
                {"f": form},
            ).mappings().first()
        if not w_row:
            return None
        w_dict = {
            k: v
            for k, v in dict(w_row).items()
            if k not in ("created_at", "updated_at", "derivatives")
        }
        wid = w_dict["word_id"]

        ms = conn.execute(
            sa.text(
                "SELECT meaning_id, pos, cn_paraphrase, en_paraphrase "
                "FROM domain.meanings WHERE word_id=:w ORDER BY meaning_id"
            ),
            {"w": wid},
        ).mappings().all()
        meanings: list[dict] = []
        for m in ms:
            mid = m["meaning_id"]
            sents = conn.execute(
                sa.text(
                    "SELECT form AS en, translation AS cn FROM domain.sentences "
                    "WHERE meaning_id=:mid ORDER BY sentence_id"
                ),
                {"mid": mid},
            ).all()
            meanings.append({
                "meaning_id": mid,
                "pos": m["pos"],
                "pos_name": POS_NAME.get(m["pos"], str(m["pos"])),
                "cn_paraphrase": m["cn_paraphrase"],
                "en_paraphrase": (m["en_paraphrase"] or "")[:CFG.EN_PARAPHRASE_CHAR_LIMIT],
                "examples": [{"en": s[0], "cn": s[1]} for s in sents],
            })
        w_dict["meanings"] = meanings

        mn = conn.execute(
            sa.text("SELECT content FROM domain.mnemonics WHERE word_id=:w"),
            {"w": wid},
        ).first()
        if mn:
            content = mn[0] if isinstance(mn[0], dict) else json.loads(mn[0])
            w_dict["mnemonic"] = content
    return w_dict


def parse_llm_text(text: str) -> Any | None:
    """Lenient JSON parser used to decode haiku/opus responses.

    Returns None if parse_llm_json raises (which has its own retry of
    various markdown-fence / trailing-comma salvage strategies). Errors
    go to the caller's `opus_parse_err` jsonl field instead of crashing.
    """
    from wordforge.stages._llm_base import parse_llm_json
    try:
        return parse_llm_json(text)
    except ValueError:
        return None
