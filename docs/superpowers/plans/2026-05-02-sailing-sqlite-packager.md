# Sailing SQLite Packager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 写一个可重复运行的打包脚本,把 prod `domain.*` 全量投影成 word-v1 JSON,写入 `words.db` 并 zip 到前端仓库既定路径;同时补全 `_POS_MAP`(`num`/`art`/`phrasal_verb`)。

**Architecture:** 分纯函数模块(可单测) + 薄 CLI 胶水。核心 `builder.py` 负责 word-v1 对象构建(从扁平 row 组装嵌套 dict),`packager.py` 负责 SQLite + zip IO,`cli.py` 负责参数、日志、DB 连接、全量流水线编排。所有业务规则在 builder 纯函数里测,不需要连 DB。

**Tech Stack:** Python 3.12、SQLAlchemy(读 prod PG)、`sqlite3` stdlib、`zipfile` stdlib、`pytest`。

**Spec:** `docs/superpowers/specs/2026-05-02-sailing-sqlite-packager-design.md`

---

## File Structure

**新增**:
- `scripts/packaging/__init__.py` — 空,让目录成 package。
- `scripts/packaging/pos_map.py` — POS 反映射表(单表 + 查询函数),~40 行。
- `scripts/packaging/builder.py` — 纯函数:扁平 row → word-v1 dict,~180 行。
- `scripts/packaging/packager.py` — SQLite + zip 写入,~80 行。
- `scripts/packaging/export_sailing_sqlite.py` — CLI + 全量编排,~120 行。
- `scripts/packaging/README.md` — 使用说明。
- `tests/packaging/__init__.py` — 空。
- `tests/packaging/test_pos_map.py` — pos 映射单测。
- `tests/packaging/test_builder.py` — builder 纯函数单测(无 DB)。
- `tests/packaging/test_packager.py` — SQLite + zip 写入端到端(用内存/临时目录)。

**修改**:
- `src/wordforge/stages/export.py:454` — `_POS_MAP` 补三个 key。
- `tests/stages/test_export.py` — 追加 3 条 assertion 覆盖新 key。

---

## Task Index

- Task 1: 扩展 `_POS_MAP`(`num`/`art`/`phrasal_verb`)+ 测试
- Task 2: `scripts/packaging/pos_map.py` 反映射表 + 测试
- Task 3: `scripts/packaging/builder.py` pos_meanings 拆分 + 测试
- Task 4: `scripts/packaging/builder.py` mnemonics.content 提取 + 测试
- Task 5: `scripts/packaging/builder.py` 单 word → word-v1 dict 组装 + 测试
- Task 6: `scripts/packaging/packager.py` SQLite 写入 + 测试
- Task 7: `scripts/packaging/packager.py` zip 打包 + 测试
- Task 8: `scripts/packaging/export_sailing_sqlite.py` CLI + 全量流水线
- Task 9: `scripts/packaging/README.md` 说明文档
- Task 10: 真机跑一遍 prod,验收

---

### Task 1: 扩展 `_POS_MAP`

**Files:**
- Modify: `src/wordforge/stages/export.py:454`
- Test: `tests/stages/test_export.py` (append at end)

- [ ] **Step 1: Write the failing test**

在 `tests/stages/test_export.py` 末尾追加:

```python
from wordforge.stages.export import _POS_MAP


def test_pos_map_has_extended_keys():
    """Spec §5.2: _POS_MAP must include num / art / phrasal_verb."""
    assert _POS_MAP["num"] == 9
    assert _POS_MAP["art"] == 10
    assert _POS_MAP["phrasal_verb"] == 201
    # Existing keys unchanged
    assert _POS_MAP["n"] == 1
    assert _POS_MAP["interj"] == 8
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/allen/code/jiyuan/backend/word_forge && \
  .venv/bin/pytest tests/stages/test_export.py::test_pos_map_has_extended_keys -v
```
Expected: FAIL with `KeyError: 'num'`

- [ ] **Step 3: Update `_POS_MAP`**

在 `src/wordforge/stages/export.py` 找到 line 454:

```python
_POS_MAP = {"n": 1, "v": 2, "adj": 3, "adv": 4, "prep": 5, "conj": 6, "pron": 7, "interj": 8}
```

改为:

```python
_POS_MAP = {
    "n": 1, "v": 2, "adj": 3, "adv": 4,
    "prep": 5, "conj": 6, "pron": 7, "interj": 8,
    "num": 9, "art": 10,
    "phrasal_verb": 201,
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/stages/test_export.py::test_pos_map_has_extended_keys -v
```
Expected: PASS

- [ ] **Step 5: Run full export test file to confirm no regression**

```bash
.venv/bin/pytest tests/stages/test_export.py -v
```
Expected: all existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/wordforge/stages/export.py tests/stages/test_export.py
git commit -m "feat(export): extend _POS_MAP with num/art/phrasal_verb"
```

---

### Task 2: `pos_map.py` 反映射表

**Files:**
- Create: `scripts/packaging/__init__.py` (empty)
- Create: `scripts/packaging/pos_map.py`
- Create: `tests/packaging/__init__.py` (empty)
- Create: `tests/packaging/test_pos_map.py`

- [ ] **Step 1: Create empty `__init__.py` files**

```bash
mkdir -p scripts/packaging tests/packaging
: > scripts/packaging/__init__.py
: > tests/packaging/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/packaging/test_pos_map.py`:

```python
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
    # Unknown pos ints (future-proofing) must not crash
    assert pos_display(999) == ("", "")
```

- [ ] **Step 3: Run test to verify it fails**

```bash
.venv/bin/pytest tests/packaging/test_pos_map.py -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 4: Write `pos_map.py`**

Create `scripts/packaging/pos_map.py`:

```python
"""POS reverse mapping: domain.meanings.pos (SMALLINT) → word-v1 strings.

Spec: docs/superpowers/specs/2026-05-02-sailing-sqlite-packager-design.md §5.1
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

# DB pos int → (pos_en, pos_cn) for word-v1 JSON
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
```

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/packaging/test_pos_map.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add scripts/packaging/__init__.py scripts/packaging/pos_map.py \
        tests/packaging/__init__.py tests/packaging/test_pos_map.py
git commit -m "feat(packaging): pos reverse mapping for word-v1"
```

---

### Task 3: `builder.py` — `split_pos_meanings`

**Files:**
- Create: `scripts/packaging/builder.py`
- Create: `tests/packaging/test_builder.py`

- [ ] **Step 1: Write the failing test**

Create `tests/packaging/test_builder.py`:

```python
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
    # 首尾空白去掉;空段丢弃
    assert split_pos_meanings(" a ; ; b ") == ["a", "b"]
    assert split_pos_meanings("；a；") == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/packaging/test_builder.py -v
```
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Create `builder.py` with `split_pos_meanings`**

Create `scripts/packaging/builder.py`:

```python
"""Pure functions that project flat DB rows → word-v1 nested dicts.

Spec: docs/superpowers/specs/2026-05-02-sailing-sqlite-packager-design.md §3-§6

Kept as pure functions (no DB, no IO) so the full mapping rules are unit-testable
without Postgres. The CLI layer feeds pre-fetched rows in.
"""

from __future__ import annotations

import re

_SEMI_RE = re.compile(r"[；;]")


def split_pos_meanings(cn: str | None) -> list[str]:
    """Spec §6 Q2(b): split cn_paraphrase on full/half-width semicolon only.

    Commas (，/,) and ideographic comma 、 stay inside each segment. Strip
    whitespace per segment and drop empty segments.
    """
    if not cn or not cn.strip():
        return []
    parts = _SEMI_RE.split(cn)
    return [p.strip() for p in parts if p.strip()]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/packaging/test_builder.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/packaging/builder.py tests/packaging/test_builder.py
git commit -m "feat(packaging): split_pos_meanings on semicolons only"
```

---

### Task 4: `builder.py` — `extract_mnemonic_text`

**Files:**
- Modify: `scripts/packaging/builder.py`
- Modify: `tests/packaging/test_builder.py`

- [ ] **Step 1: Append failing tests**

在 `tests/packaging/test_builder.py` 末尾追加:

```python
from scripts.packaging.builder import extract_mnemonic_text


def test_extract_mnemonic_text_dict_with_text():
    assert extract_mnemonic_text({"kind": "phonetic", "text": "abc"}) == "abc"


def test_extract_mnemonic_text_dict_without_text():
    # 非 {kind,text} 形状 → 空串
    assert extract_mnemonic_text({"kind": "phonetic"}) == ""
    assert extract_mnemonic_text({"text": None}) == ""
    assert extract_mnemonic_text({"text": 42}) == ""  # text 非 str


def test_extract_mnemonic_text_none_or_non_dict():
    # CacheStore/driver 理论上会把 JSONB 自动解成 dict;防御 str / None / list
    assert extract_mnemonic_text(None) == ""
    assert extract_mnemonic_text("raw string") == ""
    assert extract_mnemonic_text([]) == ""


def test_extract_mnemonic_text_json_string_fallback():
    # 如果驱动把 JSONB 原样返回 str(不太可能),也能抽出 text
    assert extract_mnemonic_text('{"kind":"phonetic","text":"abc"}') == "abc"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/packaging/test_builder.py -v
```
Expected: FAIL with `ImportError: cannot import name 'extract_mnemonic_text'`.

- [ ] **Step 3: Add `extract_mnemonic_text` to `builder.py`**

在 `scripts/packaging/builder.py` 顶部 import 区添加:

```python
import json
import logging
from typing import Any

_logger = logging.getLogger(__name__)
```

在文件末尾追加函数:

```python
def extract_mnemonic_text(content: Any) -> str:
    """Spec §4: domain.mnemonics.content is JSONB {"kind","text"}; return text.

    Defensive: missing/non-str text → "" with warning. Accepts both dict
    (normal driver behavior) and raw JSON str (driver fallback).
    """
    if content is None:
        return ""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (ValueError, TypeError):
            _logger.warning("mnemonic.content is str but not JSON: %r", content[:80])
            return ""
    if not isinstance(content, dict):
        _logger.warning("mnemonic.content is %s, expected dict", type(content).__name__)
        return ""
    text = content.get("text")
    if not isinstance(text, str) or not text:
        _logger.warning("mnemonic.content.text missing or non-str: keys=%r", list(content.keys()))
        return ""
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/packaging/test_builder.py -v
```
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/packaging/builder.py tests/packaging/test_builder.py
git commit -m "feat(packaging): extract_mnemonic_text with defensive fallback"
```

---

### Task 5: `builder.py` — `build_word_payload`

组装整个 word 节点;核心业务函数。

**Files:**
- Modify: `scripts/packaging/builder.py`
- Modify: `tests/packaging/test_builder.py`

- [ ] **Step 1: Append failing tests**

在 `tests/packaging/test_builder.py` 末尾追加:

```python
from scripts.packaging.builder import build_word_payload


def _row(**overrides):
    # Convenience: domain.words row as dict
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
    assert out["phonetic_uk"] == {"form": "[həˈləʊ]", "audio": ""}  # NULL audio_uk → ""
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
    out = build_word_payload(w, meanings=meanings, sentences_by_mid=sentences_by_mid, mnemonics=[])
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
    assert s0 == {"id": 9001, "user_group": 0, "form": "Hello world",
                  "meaning": "你好世界", "audio": "", "is_collected": 0}
    # meaning without sentences → []
    assert out["meanings"][1]["sentences"] == []
    # Unknown/NULL pos → empty strings
    assert out["meanings"][1]["pos_en"] == ""
    assert out["meanings"][1]["pos_cn"] == ""


def test_build_word_payload_with_mnemonics():
    w = _row()
    mnem = [{"mnemonic_id": 700, "type": 1, "content": {"kind": "phonetic", "text": "谐音:哈喽"}}]
    out = build_word_payload(w, meanings=[], sentences_by_mid={}, mnemonics=mnem)
    assert len(out["mnemonics"]) == 1
    m = out["mnemonics"][0]
    assert m == {"id": 700, "type": 1, "user_group": 0,
                 "creator": {}, "is_pinned": 0, "content": "谐音:哈喽"}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/packaging/test_builder.py -v
```
Expected: FAIL with `ImportError: cannot import name 'build_word_payload'`.

- [ ] **Step 3: Implement `build_word_payload`**

在 `scripts/packaging/builder.py` 末尾追加:

```python
from scripts.packaging.pos_map import pos_display  # noqa: E402  — circular-safe

WordRow = dict[str, Any]
MeaningRow = dict[str, Any]
SentenceRow = dict[str, Any]
MnemonicRow = dict[str, Any]


def _phonetic_block(form: str | None, audio: str | None) -> dict[str, str]:
    return {"form": form or "", "audio": audio or ""}


def build_word_payload(
    word: WordRow,
    *,
    meanings: list[MeaningRow],
    sentences_by_mid: dict[int, list[SentenceRow]],
    mnemonics: list[MnemonicRow],
) -> dict[str, Any]:
    """Compose one word-v1 JSON object. Pure function — no DB, no IO."""
    ph_us = _phonetic_block(word.get("phonetic_us"), word.get("audio_us"))
    ph_uk = _phonetic_block(word.get("phonetic_uk"), word.get("audio_uk"))
    return {
        "id": word["word_id"],
        "type": word["type"],
        "form": word["form"],
        "phonetic_us": ph_us,
        "phonetic_uk": ph_uk,
        "meanings": [
            _build_meaning(m, sentences_by_mid.get(m["meaning_id"], []), ph_us, ph_uk)
            for m in meanings
        ],
        "mnemonics": [_build_mnemonic(mn) for mn in mnemonics],
    }


def _build_meaning(m: MeaningRow, sentences: list[SentenceRow],
                   ph_us: dict[str, str], ph_uk: dict[str, str]) -> dict[str, Any]:
    pos_en, pos_cn = pos_display(m.get("pos"))
    return {
        "id": m["meaning_id"],
        "user_group": 0,
        "pos_en": pos_en,
        "pos_cn": pos_cn,
        "phonetic_us": ph_us,
        "phonetic_uk": ph_uk,
        "pos_meanings": split_pos_meanings(m.get("cn_paraphrase")),
        "sentences": [
            {"id": s["sentence_id"], "user_group": 0,
             "form": s["form"], "meaning": s["translation"],
             "audio": "", "is_collected": 0}
            for s in sentences
        ],
    }


def _build_mnemonic(mn: MnemonicRow) -> dict[str, Any]:
    # TODO(spec §13 Q1): creator shape to be confirmed by frontend
    return {
        "id": mn["mnemonic_id"],
        "type": mn["type"],
        "user_group": 0,
        "creator": {},
        "is_pinned": 0,
        "content": extract_mnemonic_text(mn.get("content")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/packaging/test_builder.py -v
```
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/packaging/builder.py tests/packaging/test_builder.py
git commit -m "feat(packaging): build_word_payload assembles word-v1 json"
```

---

### Task 6: `packager.py` — `write_sqlite`

**Files:**
- Create: `scripts/packaging/packager.py`
- Create: `tests/packaging/test_packager.py`

- [ ] **Step 1: Write the failing test**

Create `tests/packaging/test_packager.py`:

```python
"""SQLite + zip IO for the packager."""

import json
import sqlite3
from pathlib import Path

import pytest

from scripts.packaging.packager import write_sqlite


def test_write_sqlite_creates_expected_schema(tmp_path: Path):
    db_path = tmp_path / "words.db"
    rows = [(1, '{"id":1,"form":"hello"}'), (2, '{"id":2,"form":"world"}')]
    write_sqlite(db_path, rows)

    conn = sqlite3.connect(db_path)
    try:
        schema = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='word'"
        ).fetchone()[0]
        assert "word_id" in schema
        assert "word_json" in schema

        count = conn.execute("SELECT COUNT(*) FROM word").fetchone()[0]
        assert count == 2

        row = conn.execute("SELECT word_json FROM word WHERE word_id=1").fetchone()
        assert json.loads(row[0])["form"] == "hello"
    finally:
        conn.close()


def test_write_sqlite_overwrites_existing_file(tmp_path: Path):
    db_path = tmp_path / "words.db"
    db_path.write_bytes(b"garbage")  # pretend leftover
    rows = [(42, '{"id":42}')]
    write_sqlite(db_path, rows)
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM word").fetchone()[0]
        assert count == 1
    finally:
        conn.close()


def test_write_sqlite_accepts_iterator(tmp_path: Path):
    db_path = tmp_path / "words.db"
    # Ensure we don't require list — generator must work for memory efficiency
    def gen():
        for i in range(10):
            yield (i, f'{{"id":{i}}}')
    write_sqlite(db_path, gen())
    conn = sqlite3.connect(db_path)
    try:
        count = conn.execute("SELECT COUNT(*) FROM word").fetchone()[0]
        assert count == 10
    finally:
        conn.close()


def test_write_sqlite_rejects_bad_rows(tmp_path: Path):
    """Guard against accidentally writing rows that are not (int, str)."""
    db_path = tmp_path / "words.db"
    with pytest.raises((sqlite3.InterfaceError, TypeError)):
        write_sqlite(db_path, [(1, {"not": "a string"})])  # type: ignore[list-item]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/packaging/test_packager.py -v
```
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `write_sqlite`**

Create `scripts/packaging/packager.py`:

```python
"""SQLite + zip IO for the sailing words packager.

Spec §7. Uses stdlib sqlite3 + zipfile only.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

# TODO(spec §13): runtime pragma tuning (VACUUM / page_size / journal_mode=DELETE)
# pending flutter-side startup-cost measurements.
_CREATE_TABLE = """
CREATE TABLE word (
  word_id INTEGER PRIMARY KEY,
  word_json TEXT NOT NULL
)
"""

_INSERT = "INSERT INTO word (word_id, word_json) VALUES (?, ?)"


def write_sqlite(db_path: Path, rows: Iterable[tuple[int, str]], *, batch_size: int = 5000) -> int:
    """Write (word_id, word_json) tuples into a fresh SQLite file.

    Overwrites any existing file at db_path. Returns the number of rows inserted.
    """
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        # Bulk-insert only pragmas (do NOT ship to flutter — TODO in spec §13)
        conn.execute("PRAGMA synchronous = OFF")
        conn.execute("PRAGMA journal_mode = MEMORY")
        conn.execute(_CREATE_TABLE)

        total = 0
        batch: list[tuple[int, str]] = []
        for row in rows:
            batch.append(row)
            if len(batch) >= batch_size:
                conn.executemany(_INSERT, batch)
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(_INSERT, batch)
            total += len(batch)
        conn.commit()
        return total
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/packaging/test_packager.py -v
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/packaging/packager.py tests/packaging/test_packager.py
git commit -m "feat(packaging): write_sqlite streams rows into fresh db file"
```

---

### Task 7: `packager.py` — `zip_db`

**Files:**
- Modify: `scripts/packaging/packager.py`
- Modify: `tests/packaging/test_packager.py`

- [ ] **Step 1: Append failing tests**

在 `tests/packaging/test_packager.py` 末尾追加:

```python
import zipfile

from scripts.packaging.packager import zip_db


def test_zip_db_produces_zip_with_words_db_entry(tmp_path: Path):
    src = tmp_path / "source.db"
    src.write_bytes(b"fake sqlite bytes")
    out = tmp_path / "out.zip"

    zip_db(src, out)

    assert out.exists()
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert names == ["words.db"]  # single entry, fixed name
        assert z.read("words.db") == b"fake sqlite bytes"


def test_zip_db_overwrites_existing(tmp_path: Path):
    src = tmp_path / "source.db"
    src.write_bytes(b"v2")
    out = tmp_path / "out.zip"
    out.write_bytes(b"old-zip-bytes")  # leftover from previous run
    zip_db(src, out)
    with zipfile.ZipFile(out) as z:
        assert z.read("words.db") == b"v2"


def test_zip_db_creates_parent_dir(tmp_path: Path):
    src = tmp_path / "source.db"
    src.write_bytes(b"x")
    out = tmp_path / "nested" / "dir" / "out.zip"
    zip_db(src, out)
    assert out.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/packaging/test_packager.py -v
```
Expected: new 3 tests FAIL with `ImportError: cannot import name 'zip_db'`.

- [ ] **Step 3: Implement `zip_db`**

在 `scripts/packaging/packager.py` 顶部 import 区追加:

```python
import zipfile
```

在文件末尾追加:

```python
def zip_db(db_path: Path, zip_path: Path) -> None:
    """Zip db_path into zip_path with a fixed internal entry name 'words.db'.

    Overwrites any existing zip at zip_path. Creates parent dir if missing.
    """
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(db_path, arcname="words.db")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/packaging/test_packager.py -v
```
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add scripts/packaging/packager.py tests/packaging/test_packager.py
git commit -m "feat(packaging): zip_db packs sqlite into words.db.zip"
```

---

### Task 8: `export_sailing_sqlite.py` — CLI + 全量流水线

CLI 胶水:连 prod,批量读 4 张表,内存聚合,喂给 builder → packager。

**Files:**
- Create: `scripts/packaging/export_sailing_sqlite.py`

(该模块集中处理 DB IO + 编排;纯函数已在 builder 测过,本脚本不走单测 — Task 10 真机验收覆盖。)

- [ ] **Step 1: 实现 `export_sailing_sqlite.py`**

Create `scripts/packaging/export_sailing_sqlite.py`:

```python
"""CLI: export prod domain.* → flutter-friendly SQLite zip.

Spec: docs/superpowers/specs/2026-05-02-sailing-sqlite-packager-design.md

Usage:
    source ~/.wordforge/prod.env
    ./.venv/bin/python scripts/packaging/export_sailing_sqlite.py [--output PATH] [--limit N] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import sqlalchemy as sa

from scripts.packaging.builder import build_word_payload
from scripts.packaging.packager import write_sqlite, zip_db

_DEFAULT_OUTPUT = Path(
    "/Users/allen/code/jiyuan/frontent/sailing_words/assets/database/words.db.zip"
)

_log = logging.getLogger("packaging")


def _fetch_all(engine: sa.Engine, limit: int | None) -> tuple[list[dict], dict[int, list[dict]], dict[int, list[dict]], dict[int, list[dict]]]:
    """Run the 4 SELECTs from spec §7.3. Aggregate by word_id / meaning_id."""
    words_sql = (
        "SELECT word_id, type, form, phonetic_us, phonetic_uk, audio_us, audio_uk "
        "FROM domain.words ORDER BY word_id"
    )
    if limit is not None:
        words_sql += f" LIMIT {int(limit)}"

    with engine.connect() as conn:
        _log.info("fetching domain.words ...")
        words = [dict(r._mapping) for r in conn.execute(sa.text(words_sql)).all()]
        word_ids = [w["word_id"] for w in words]
        _log.info("  got %d words", len(words))

        if not word_ids:
            return words, {}, {}, {}

        _log.info("fetching domain.meanings ...")
        meanings_by_wid: dict[int, list[dict]] = defaultdict(list)
        rows = conn.execute(
            sa.text(
                "SELECT meaning_id, word_id, pos, cn_paraphrase "
                "FROM domain.meanings WHERE word_id = ANY(:ids) "
                "ORDER BY word_id, meaning_id"
            ),
            {"ids": word_ids},
        ).all()
        for r in rows:
            meanings_by_wid[r.word_id].append(dict(r._mapping))
        meaning_ids = [m["meaning_id"] for ms in meanings_by_wid.values() for m in ms]
        _log.info("  got %d meanings across %d words", len(rows), len(meanings_by_wid))

        _log.info("fetching domain.sentences ...")
        sentences_by_mid: dict[int, list[dict]] = defaultdict(list)
        if meaning_ids:
            rows = conn.execute(
                sa.text(
                    "SELECT sentence_id, meaning_id, form, translation "
                    "FROM domain.sentences WHERE meaning_id = ANY(:ids) "
                    "ORDER BY meaning_id, sentence_id"
                ),
                {"ids": meaning_ids},
            ).all()
            for r in rows:
                sentences_by_mid[r.meaning_id].append(dict(r._mapping))
            _log.info("  got %d sentences", len(rows))

        _log.info("fetching domain.mnemonics ...")
        mnemonics_by_wid: dict[int, list[dict]] = defaultdict(list)
        rows = conn.execute(
            sa.text(
                "SELECT mnemonic_id, word_id, type, content "
                "FROM domain.mnemonics WHERE word_id = ANY(:ids) "
                "ORDER BY word_id, mnemonic_id"
            ),
            {"ids": word_ids},
        ).all()
        for r in rows:
            mnemonics_by_wid[r.word_id].append(dict(r._mapping))
        _log.info("  got %d mnemonics", len(rows))

    return words, dict(meanings_by_wid), dict(sentences_by_mid), dict(mnemonics_by_wid)


def _build_all(words, meanings_by_wid, sentences_by_mid, mnemonics_by_wid):
    for w in words:
        wid = w["word_id"]
        payload = build_word_payload(
            w,
            meanings=meanings_by_wid.get(wid, []),
            sentences_by_mid=sentences_by_mid,
            mnemonics=mnemonics_by_wid.get(wid, []),
        )
        yield wid, json.dumps(payload, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    p.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    p.add_argument("--limit", type=int, default=None, help="debug: only first N words")
    p.add_argument("--dry-run", action="store_true", help="build JSON only, do not write files")
    args = p.parse_args(argv)

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set (did you `source ~/.wordforge/prod.env`?)")

    t0 = time.perf_counter()
    engine = sa.create_engine(url, future=True)
    try:
        words, meanings_by_wid, sentences_by_mid, mnemonics_by_wid = _fetch_all(engine, args.limit)
    finally:
        engine.dispose()

    rows = list(_build_all(words, meanings_by_wid, sentences_by_mid, mnemonics_by_wid))
    _log.info("built %d word payloads", len(rows))

    if args.dry_run:
        _log.info("--dry-run: skip sqlite + zip. total time=%.1fs", time.perf_counter() - t0)
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "words.db"
        n = write_sqlite(db_path, rows)
        db_size_mb = db_path.stat().st_size / 1_000_000
        _log.info("sqlite written: %d rows, %.1f MB", n, db_size_mb)
        zip_db(db_path, args.output)

    zip_size_mb = args.output.stat().st_size / 1_000_000
    _log.info("zip written: %s (%.1f MB)", args.output, zip_size_mb)
    _log.info("total time=%.1fs", time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Smoke test dry-run with a tiny limit (no zip write)**

```bash
source ~/.wordforge/prod.env && \
  .venv/bin/python scripts/packaging/export_sailing_sqlite.py --limit 5 --dry-run
```
Expected: logs `got 5 words` → `built 5 word payloads` → `--dry-run: skip sqlite + zip` with non-zero total time, exit 0.

- [ ] **Step 3: Commit**

```bash
git add scripts/packaging/export_sailing_sqlite.py
git commit -m "feat(packaging): cli + prod fetch + pipeline orchestration"
```

---

### Task 9: `README.md`

**Files:**
- Create: `scripts/packaging/README.md`

- [ ] **Step 1: Create README**

Create `scripts/packaging/README.md`:

```markdown
# Sailing Words SQLite Packager

打包 prod `domain.*` → flutter 可读的 `words.db.zip`。

## 运行

```bash
source ~/.wordforge/prod.env  # 载入 prod 只读 DATABASE_URL
.venv/bin/python scripts/packaging/export_sailing_sqlite.py
```

默认输出到
`/Users/allen/code/jiyuan/frontent/sailing_words/assets/database/words.db.zip`。

## 参数

- `--output PATH` 自定义输出 zip 路径。
- `--limit N` 只打前 N 个词(调试)。
- `--dry-run` 只构 JSON,不写 SQLite 和 zip。

## 数据结构

输出 SQLite 的表:

```sql
CREATE TABLE word (
  word_id INTEGER PRIMARY KEY,
  word_json TEXT NOT NULL
);
```

`word_json` 遵循 word-v1 约定,schema 在飞书 wiki
`https://lpt2q1lbzh.feishu.cn/wiki/U0w0wzWvdihbH1kUYltc1kjPn6c`,详细映射规则在
`docs/superpowers/specs/2026-05-02-sailing-sqlite-packager-design.md`。

## 坑 & TODO

- **不要并行跑本脚本和 pytest** —— CLAUDE.md 的 DB 隔离只挡 pytest 改写 prod,
  pytest 启动本身会撞 alembic migration 轨迹。
- 更新 `_POS_MAP` 时(`src/wordforge/stages/export.py`),
  `scripts/packaging/pos_map.py` 的反映射表要同步改。
- 本脚本 zip 内的条目名是 `words.db`;前端要对齐这个名字(旧版是 `sailing.db`)。
- Q1 `mnemonics[].creator` 先空对象 `{}`,等前端给出形状后回填;见 spec §13。
- 运行时 SQLite pragma 优化(VACUUM / page_size / journal_mode=DELETE)等前端实测
  启动耗时后再定;见 spec §7.1 TODO 和 §13。
```

- [ ] **Step 2: Commit**

```bash
git add scripts/packaging/README.md
git commit -m "docs(packaging): readme for sailing packager"
```

---

### Task 10: 真机跑 prod,验收

**Files:** 无(运行 + 人工验证)

- [ ] **Step 1: Run full pipeline on prod**

```bash
source ~/.wordforge/prod.env && \
  .venv/bin/python scripts/packaging/export_sailing_sqlite.py
```
Expected: 退出码 0,日志打出 word 数 == 121057(或当时真实值)+ sqlite/zip 大小 + 总耗时。

- [ ] **Step 2: 验收 #1-#3(spec §11)— 文件与计数**

```bash
# 文件存在且能解压
unzip -l /Users/allen/code/jiyuan/frontent/sailing_words/assets/database/words.db.zip

# 比对行数
rm -rf /tmp/pack_verify && mkdir /tmp/pack_verify && cd /tmp/pack_verify && \
  unzip /Users/allen/code/jiyuan/frontent/sailing_words/assets/database/words.db.zip && \
  sqlite3 words.db "SELECT COUNT(*) FROM word;"

source ~/.wordforge/prod.env && \
  .venv/bin/python -c "import os, sqlalchemy as sa; e=sa.create_engine(os.environ['DATABASE_URL']); print(e.connect().execute(sa.text('SELECT COUNT(*) FROM domain.words')).scalar())"
```
Expected: 两数字相等。

- [ ] **Step 3: 验收 #4-#5 — JSON 合法性 + 子字段非空**

```bash
cd /tmp/pack_verify && python3 -c "
import sqlite3, json, random
conn = sqlite3.connect('words.db')
rows = conn.execute('SELECT word_json FROM word').fetchall()
sampled = random.sample(rows, 10)
required = {'id','type','form','phonetic_us','phonetic_uk','meanings','mnemonics'}
has_m = has_mn = 0
for (j,) in sampled:
    o = json.loads(j)
    assert required <= set(o.keys()), f'missing keys: {required - set(o.keys())}'
    if o['meanings']: has_m += 1
    if o['mnemonics']: has_mn += 1
print(f'10 rows ok; meanings_nonempty={has_m}, mnemonics_nonempty={has_mn}')
assert has_m >= 1 and has_mn >= 1
"
```
Expected: `10 rows ok; meanings_nonempty≥1, mnemonics_nonempty≥1`。

- [ ] **Step 4: 验收 #6 — hello 精确 diff**

```bash
source ~/.wordforge/prod.env && \
  .venv/bin/python -c "
import os, json, sqlite3, sqlalchemy as sa
eng = sa.create_engine(os.environ['DATABASE_URL'])
with eng.connect() as c:
    hid = c.execute(sa.text(\"SELECT word_id FROM domain.words WHERE form='hello' AND type=1\")).scalar()
    mcount = c.execute(sa.text('SELECT COUNT(*) FROM domain.meanings WHERE word_id=:w'), {'w': hid}).scalar()
    mnem_text = c.execute(sa.text(\"SELECT content->>'text' FROM domain.mnemonics WHERE word_id=:w ORDER BY mnemonic_id LIMIT 1\"), {'w': hid}).scalar()

sconn = sqlite3.connect('/tmp/pack_verify/words.db')
payload = json.loads(sconn.execute('SELECT word_json FROM word WHERE word_id=?', (hid,)).fetchone()[0])
assert payload['id'] == hid and payload['form'] == 'hello' and payload['type'] == 1
assert len(payload['meanings']) == mcount, f'meanings: payload={len(payload[\"meanings\"])} db={mcount}'
assert payload['mnemonics'][0]['content'] == mnem_text, f'mnem mismatch'
print('hello diff OK')
"
```
Expected: `hello diff OK`。

- [ ] **Step 5: 更新 TODO.md 记录新出口**

在项目 `TODO.md` 相关段落追加一行指向新脚本(可选,让未来的人更容易找到)。

- [ ] **Step 6: Commit(仅验收过程中产生的日志文档,若无就跳过)**

```bash
git status -s
# 若只有本轮生成的 zip 而没有代码变更,无需 commit(zip 在前端仓库)
```

验收通过即 plan 完成。

---

## Self-Review 检查

已对 Task 1-10 vs spec §1-§13 逐项核对:

- spec §1 "要做":Task 2-8(打包脚本)+ Task 1(扩 `_POS_MAP`)+ Task 9(README) ✓
- spec §2 数据源:Task 8 `_fetch_all` 四个 SELECT ✓
- spec §3 word-v1 schema:Task 5 `build_word_payload` 覆盖全字段 ✓
- spec §4 字段映射表:Task 5 测试逐字段覆盖 ✓
- spec §5.1 反映射:Task 2 测 1-10/201/NULL/未知 ✓
- spec §5.2 正映射扩展:Task 1 ✓
- spec §6 pos_meanings 拆分:Task 3 覆盖 Q2(b) 的 5 个示例 ✓
- spec §7.1 SQLite schema:Task 6 `_CREATE_TABLE` ✓;运行时 pragma TODO 已在代码注释里标注
- spec §7.2 打包流程:Task 6 写入 + Task 7 zip + Task 8 临时目录编排 ✓
- spec §7.3 SQL 大纲:Task 8 `_fetch_all` ✓
- spec §8 CLI:Task 8 argparse 3 个参数 ✓
- spec §9 文件布局:Task 2/6/8/9 创建 4 个文件 ✓
- spec §10 幂等:Task 6 的 `unlink` + Task 7 的 `unlink` + Task 8 的 tempdir ✓
- spec §11 验收:Task 10 step 2-4 分别覆盖 #1-#6;#7(尺寸量级)在 Task 10 step 1 的日志里肉眼确认
- spec §12 风险:注释里已标,无代码动作
- spec §13 TODO:spec §7.1 pragma TODO 已在 Task 6 代码里挂;Q1 creator 在 Task 5 代码里挂;serving 对齐属长期项,无代码动作

无 placeholder,无类型不一致。
