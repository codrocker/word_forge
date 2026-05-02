"""Spec §5.1: DB pos int → (pos_en, pos_cn) string pair."""

from scripts.packaging.pos_map import pos_display


def test_pos_display_known_values():
    assert pos_display(1) == ("n.", "名词")
    assert pos_display(2) == ("v.", "动词")
    assert pos_display(8) == ("interj.", "感叹词")
    assert pos_display(9) == ("num.", "数词")
    assert pos_display(10) == ("art.", "冠词")
    assert pos_display(201) == ("phrase", "短语动词")


def test_pos_display_null_falls_back_to_empty():
    assert pos_display(None) == ("", "")


def test_pos_display_unknown_falls_back_to_empty():
    assert pos_display(999) == ("", "")
