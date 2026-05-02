"""Pure-function builders for word-v1 JSON."""

from scripts.packaging.builder import split_pos_meanings


def test_split_pos_meanings_none_and_empty():
    assert split_pos_meanings(None) == []
    assert split_pos_meanings("") == []
    assert split_pos_meanings("   ") == []


def test_split_pos_meanings_no_separator_keeps_whole():
    # Spec §6 Q2(b): 全/半角逗号和顿号不拆
    assert split_pos_meanings("黑体，粗体") == ["黑体，粗体"]
    assert split_pos_meanings("[wear 过去分词] 穿，戴") == ["[wear 过去分词] 穿，戴"]
    assert split_pos_meanings("a, b, c") == ["a, b, c"]
    assert split_pos_meanings("甲、乙、丙") == ["甲、乙、丙"]


def test_split_pos_meanings_full_width_semi():
    assert split_pos_meanings("见面；相遇；遇到") == ["见面", "相遇", "遇到"]


def test_split_pos_meanings_half_width_semi():
    assert split_pos_meanings("foo;bar;baz") == ["foo", "bar", "baz"]


def test_split_pos_meanings_strip_and_drop_empty():
    # 混用全半角分号
    assert split_pos_meanings(" a ； ; b ") == ["a", "b"]
    assert split_pos_meanings("；a；") == ["a"]



from scripts.packaging.builder import extract_mnemonic_text


def test_extract_mnemonic_text_dict_with_text():
    assert extract_mnemonic_text({"kind": "phonetic", "text": "abc"}) == "abc"


def test_extract_mnemonic_text_dict_without_text():
    # 非 {kind, text} 形状 → 空串
    assert extract_mnemonic_text({"kind": "phonetic"}) == ""
    assert extract_mnemonic_text({"text": None}) == ""
    assert extract_mnemonic_text({"text": 42}) == ""  # text 非 str


def test_extract_mnemonic_text_none_or_non_dict():
    # CacheStore/driver 理论上会把 JSONB 自动解成 dict;防御 str / None / list
    assert extract_mnemonic_text(None) == ""
    assert extract_mnemonic_text("raw string") == ""
    assert extract_mnemonic_text([]) == ""


def test_extract_mnemonic_text_json_string_fallback():
    # 如果驱动把 JSONB 原样返回 str,也能抽出 text
    assert extract_mnemonic_text('{"kind":"phonetic","text":"abc"}') == "abc"
