"""Pure-function builders for word-v1 JSON."""

from scripts.packaging.builder import (
    build_word_payload,
    extract_mnemonic_text,
    split_pos_meanings,
)


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


def _row(**overrides):
    base = {
        "word_id": 100001, "type": 1, "form": "hello",
        "phonetic_us": "[həˈloʊ]", "phonetic_uk": "[həˈləʊ]",
        "audio_us": "https://a.us/hello.mp3", "audio_uk": None,
    }
    base.update(overrides)
    return base


def test_build_word_payload_minimal_no_children():
    out = build_word_payload(_row(), meanings=[], sentences_by_mid={}, mnemonics=[])
    assert out["id"] == 100001
    assert out["type"] == 1
    assert out["form"] == "hello"
    assert out["phonetic_us"] == {"form": "[həˈloʊ]", "audio": "https://a.us/hello.mp3"}
    # NULL audio_uk 走空串
    assert out["phonetic_uk"] == {"form": "[həˈləʊ]", "audio": ""}
    assert out["meanings"] == []
    assert out["mnemonics"] == []


def test_build_word_payload_null_phonetic_fields():
    w = _row(phonetic_us=None, phonetic_uk=None, audio_us=None, audio_uk=None)
    out = build_word_payload(w, meanings=[], sentences_by_mid={}, mnemonics=[])
    assert out["phonetic_us"] == {"form": "", "audio": ""}
    assert out["phonetic_uk"] == {"form": "", "audio": ""}


def test_build_word_payload_with_meanings_and_sentences():
    w = _row()
    meanings = [
        {"meaning_id": 500, "pos": 8, "cn_paraphrase": "你好；您好"},
        {"meaning_id": 501, "pos": None, "cn_paraphrase": "问候"},
    ]
    sentences_by_mid = {
        500: [
            {"sentence_id": 9001, "form": "Hello world", "translation": "你好世界"},
            {"sentence_id": 9002, "form": "Say hello", "translation": "打招呼"},
        ],
    }
    out = build_word_payload(
        w, meanings=meanings, sentences_by_mid=sentences_by_mid, mnemonics=[]
    )
    assert len(out["meanings"]) == 2
    m0 = out["meanings"][0]
    assert m0["id"] == 500
    assert m0["user_group"] == 0
    assert m0["pos_en"] == "interj."
    assert m0["pos_cn"] == "感叹词"
    # Spec §4: meaning 级 phonetic 复用 word 级
    assert m0["phonetic_us"] == out["phonetic_us"]
    assert m0["phonetic_uk"] == out["phonetic_uk"]
    assert m0["pos_meanings"] == ["你好", "您好"]
    assert len(m0["sentences"]) == 2
    s0 = m0["sentences"][0]
    assert s0 == {
        "id": 9001, "user_group": 0, "form": "Hello world",
        "meaning": "你好世界", "audio": "", "is_collected": 0,
    }
    # meaning without sentences → []
    assert out["meanings"][1]["sentences"] == []
    # Unknown / NULL pos → empty strings
    assert out["meanings"][1]["pos_en"] == ""
    assert out["meanings"][1]["pos_cn"] == ""


def test_build_word_payload_with_mnemonics():
    w = _row()
    mnem = [{"mnemonic_id": 700, "type": 1, "content": {"kind": "phonetic", "text": "谐音:哈喽"}}]
    out = build_word_payload(w, meanings=[], sentences_by_mid={}, mnemonics=mnem)
    assert len(out["mnemonics"]) == 1
    m = out["mnemonics"][0]
    assert m == {
        "id": 700, "type": 1, "user_group": 0,
        "creator": {}, "is_pinned": 0, "content": "谐音:哈喽",
    }
