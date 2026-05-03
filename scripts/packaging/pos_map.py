"""POS reverse mapping: domain.meanings.pos (SMALLINT) → word-v1 strings."""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

_POS_DISPLAY: dict[int, tuple[str, str]] = {
    1: ("n.", "名词"),
    2: ("v.", "动词"),
    3: ("adj.", "形容词"),
    4: ("adv.", "副词"),
    5: ("prep.", "介词"),
    6: ("conj.", "连词"),
    7: ("pron.", "代词"),
    8: ("interj.", "感叹词"),
    9: ("num.", "数词"),
    10: ("art.", "冠词"),
    201: ("phrase", "短语动词"),
}


def pos_display(pos: int | None) -> tuple[str, str]:
    """Return (pos_en, pos_cn); empty strings on NULL or unknown."""
    if pos is None:
        return ("", "")
    pair = _POS_DISPLAY.get(pos)
    if pair is None:
        _logger.warning("unknown pos=%r, falling back to empty strings", pos)
        return ("", "")
    return pair
