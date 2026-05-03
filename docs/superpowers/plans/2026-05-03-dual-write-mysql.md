# Dual-write MySQL (word_forge DB) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 wordforge PG `domain.*` 的 word / meaning / sentence / mnemonic / phrase 数据镜像到新 MySQL database `word_forge`,gozero 后端过渡期可切库读。

**Architecture:** 单脚本 `scripts/replicate/mirror_to_mysql.py` 用 SQLAlchemy 读 PG、pymysql 写 MySQL;每张表有 `<name>` + `<name>_shadow` 副本,灌完 shadow 后两条 `RENAME TABLE` 原子 swap,读服务无空窗。Schema DDL 落盘 `mysql_schema.sql`,启动时 sanity check 不自动 DDL。对账脚本独立 `verify_mysql_mirror.py` count + checksum。

**Tech Stack:** Python 3.12 · SQLAlchemy 2.x · pymysql(new) · psycopg(已有) · pytest。

**Spec 引用:** `docs/superpowers/specs/2026-05-02-dual-write-mysql-design.md`

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `scripts/replicate/__init__.py` | 新建 | empty package marker |
| `scripts/replicate/mysql_schema.sql` | 新建 | 5 张主表 + 5 张 shadow 的 DDL(仓内权威,本轮对齐 wiki) |
| `scripts/replicate/init_database.sql.example` | 新建 | CREATE DATABASE + 两个账号 template(占位密码)|
| `scripts/replicate/field_mapping.py` | 新建 | 5 个纯函数 `_row_to_mysql_<table>`,输入 PG row + 关系上下文 → 输出 MySQL row dict |
| `scripts/replicate/mirror_to_mysql.py` | 新建 | 主同步脚本:stage 0-5 |
| `scripts/replicate/verify_mysql_mirror.py` | 新建 | 对账:count + checksum |
| `tests/replicate/__init__.py` | 新建 | empty package marker |
| `tests/replicate/test_field_mapping.py` | 新建 | `field_mapping.py` 纯函数单测 |
| `pyproject.toml` | Modify | 加 `pymysql>=1.1` + `cryptography>=42`(pymysql auth 依赖)|
| `.gitignore` | Modify | 排除 `init_database.sql` / `replicate_run.jsonl` / `drift_report.jsonl` |

不动:`stages/export.py`、`ingest.py`、`pipeline/*` 任何文件。

---

## Task 1: 目录骨架 + 依赖 + .gitignore

**Files:**
- Create: `scripts/replicate/__init__.py`
- Create: `tests/replicate/__init__.py`
- Modify: `pyproject.toml`
- Modify: `.gitignore`

- [ ] **Step 1: 建空目录 + `__init__.py`**

```bash
mkdir -p scripts/replicate tests/replicate
touch scripts/replicate/__init__.py tests/replicate/__init__.py
```

- [ ] **Step 2: 加 pymysql 依赖**

Run: `uv add "pymysql>=1.1" "cryptography>=42"`
Expected: `Resolved ... packages` + `Installed pymysql==1.1.x cryptography==...`

验证: `.venv/bin/python -c "import pymysql; print(pymysql.__version__)"` 打印 `1.1.x`。

- [ ] **Step 3: 追加 .gitignore 排除**

在 `.gitignore` 末尾追加:

```
# dual-write mysql (2026-05-03)
/scripts/replicate/init_database.sql
/replicate_run.jsonl
/drift_report.jsonl
```

- [ ] **Step 4: Commit**

```bash
git add scripts/replicate/__init__.py tests/replicate/__init__.py pyproject.toml uv.lock .gitignore
git commit -m "feat(replicate): scaffolding + pymysql dep"
```

---

## Task 2: mysql_schema.sql + init_database.sql.example

**Files:**
- Create: `scripts/replicate/mysql_schema.sql`
- Create: `scripts/replicate/init_database.sql.example`

- [ ] **Step 1: 写 `mysql_schema.sql`,5 张主表 DDL 按 wiki**

建 `scripts/replicate/mysql_schema.sql`,内容逐字参考 spec §4.1-§4.5 的 DDL,然后追加 §4.6 的 5 个 `CREATE TABLE <name>_shadow LIKE <name>;`。

开头加 comment:

```sql
-- scripts/replicate/mysql_schema.sql
-- Source of truth: feishu wiki https://lpt2q1lbzh.feishu.cn/wiki/wikcnQFiS6CvAj8sfXW86mK1d2G
-- Apply once, manually:
--   mysql -h 120.27.242.42 -u wordforge_writer -p word_forge < scripts/replicate/mysql_schema.sql
```

- [ ] **Step 2: 写 `init_database.sql.example`**

```sql
-- scripts/replicate/init_database.sql.example
-- Copy to init_database.sql, fill <pwd-writer> / <pwd-reader> (openssl rand -base64 24),
-- then run once as MySQL root/DBA:
--   mysql -h 120.27.242.42 -u root -p < scripts/replicate/init_database.sql
-- init_database.sql is gitignored; .example is the only thing in the repo.

CREATE DATABASE IF NOT EXISTS word_forge
  DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'wordforge_writer'@'%' IDENTIFIED BY '<pwd-writer>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, ALTER, INDEX
  ON word_forge.* TO 'wordforge_writer'@'%';

CREATE USER IF NOT EXISTS 'wordforge_reader'@'%' IDENTIFIED BY '<pwd-reader>';
GRANT SELECT ON word_forge.* TO 'wordforge_reader'@'%';

FLUSH PRIVILEGES;
```

- [ ] **Step 3: 本地 lint DDL(纯文本,不连 DB)**

Run: `.venv/bin/python -c "open('scripts/replicate/mysql_schema.sql').read()" && echo OK`
Expected: `OK`(只是确认文件可读,无语法错把 sqlfluff 这种工具按下不引入)

- [ ] **Step 4: Commit**

```bash
git add scripts/replicate/mysql_schema.sql scripts/replicate/init_database.sql.example
git commit -m "feat(replicate): MySQL schema DDL per wiki + init_database template"
```

---

## Task 3: field_mapping.py — `_row_to_mysql_word` + TDD

**Files:**
- Create: `scripts/replicate/field_mapping.py`
- Create: `tests/replicate/test_field_mapping.py`

- [ ] **Step 1: 先写失败测试**

创建 `tests/replicate/test_field_mapping.py`:

```python
"""Unit tests for scripts/replicate/field_mapping.py.

Pure functions: PG row dict + relations context -> MySQL row dict.
No real DB.
"""

from __future__ import annotations

import json

import pytest

from scripts.replicate.field_mapping import row_to_mysql_word


def test_word_basic_fields_direct_passthrough():
    pg_row = {
        "word_id": 100001,
        "type": 1,
        "form": "the",
        "phonetic_us": "ðə",
        "phonetic_uk": "ðə",
        "audio_us": "https://cdn/us/the.mp3",
        "audio_uk": None,
        "source": "pipeline:local:export_v1",
    }
    mysql_row = row_to_mysql_word(pg_row, meaning_ids=[], mnemonic_ids=[], phrase_ids=[])

    assert mysql_row["word_id"] == 100001
    assert mysql_row["type"] == 1
    assert mysql_row["form"] == "the"
    assert mysql_row["phonetic_us"] == "ðə"
    assert mysql_row["audio_uk"] is None
    assert mysql_row["source"] == "pipeline:local:export_v1"


def test_word_status_hardcoded_to_1():
    """spec §5.1: wordforge 能进此 mirror 的都是已上线,status 固定 1."""
    pg_row = {
        "word_id": 100001, "type": 1, "form": "x",
        "phonetic_us": "", "phonetic_uk": "",
        "audio_us": None, "audio_uk": None, "source": None,
    }
    row = row_to_mysql_word(pg_row, meaning_ids=[], mnemonic_ids=[], phrase_ids=[])
    assert row["status"] == 1


def test_word_phonetic_null_becomes_empty_string():
    """wiki: phonetic_us / phonetic_uk NOT NULL. NULL -> ''."""
    pg_row = {
        "word_id": 100001, "type": 1, "form": "x",
        "phonetic_us": None, "phonetic_uk": None,
        "audio_us": None, "audio_uk": None, "source": None,
    }
    row = row_to_mysql_word(pg_row, meaning_ids=[], mnemonic_ids=[], phrase_ids=[])
    assert row["phonetic_us"] == ""
    assert row["phonetic_uk"] == ""


def test_word_meanings_composed_as_object_array():
    pg_row = {
        "word_id": 100001, "type": 1, "form": "x",
        "phonetic_us": "", "phonetic_uk": "",
        "audio_us": None, "audio_uk": None, "source": None,
    }
    row = row_to_mysql_word(pg_row, meaning_ids=[200001, 200002], mnemonic_ids=[300001], phrase_ids=[])
    assert json.loads(row["meanings"]) == [{"id": 200001}, {"id": 200002}]
    assert json.loads(row["mnemonics"]) == [{"id": 300001}]
    assert row["phrases"] is None  # 空 list -> NULL(不写 "[]" 空串)


def test_word_nulls_per_wiki_tmp_null_list():
    """wiki "临时设置 NULL" 清单: plural/comparative/superlative/structure/
    third_person/present_participle/past_tense/past_participle/
    derivatives/morpheme_derivatives/family/base 都应为 None."""
    pg_row = {
        "word_id": 100001, "type": 1, "form": "x",
        "phonetic_us": "", "phonetic_uk": "",
        "audio_us": None, "audio_uk": None, "source": None,
    }
    row = row_to_mysql_word(pg_row, meaning_ids=[], mnemonic_ids=[], phrase_ids=[])
    for col in ("plural", "comparative", "superlative", "structure",
                "third_person", "present_participle", "past_tense", "past_participle",
                "derivatives", "morpheme_derivatives", "family", "base"):
        assert row[col] is None, f"{col} must be None"
```

- [ ] **Step 2: 跑测试确认 FAIL(模块不存在)**

Run: `DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test' .venv/bin/python -m pytest tests/replicate/test_field_mapping.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.replicate.field_mapping'`

- [ ] **Step 3: 实现 `field_mapping.py` 的 `row_to_mysql_word`**

创建 `scripts/replicate/field_mapping.py`:

```python
"""Pure field-mapping functions: PG domain row -> MySQL word_forge row.

Spec: docs/superpowers/specs/2026-05-02-dual-write-mysql-design.md §5
"""

from __future__ import annotations

import json

# ruff: noqa: E501


def _id_list_to_json(ids: list[int], key: str = "id") -> str | None:
    """Convert [123, 456] -> '[{"id":123},{"id":456}]' per wiki convention.
    Empty list -> None (stored as NULL in MySQL, not empty '[]' string)."""
    if not ids:
        return None
    return json.dumps([{key: i} for i in ids], separators=(",", ":"))


def row_to_mysql_word(
    pg: dict,
    *,
    meaning_ids: list[int],
    mnemonic_ids: list[int],
    phrase_ids: list[int],
) -> dict:
    """Map one domain.words row + children id lists to MySQL word_forge.word row."""
    return {
        "word_id": pg["word_id"],
        "type": pg["type"],
        "form": pg["form"],
        "phonetic_us": pg.get("phonetic_us") or "",
        "audio_us": pg.get("audio_us"),
        "phonetic_uk": pg.get("phonetic_uk") or "",
        "audio_uk": pg.get("audio_uk"),
        "meanings": _id_list_to_json(meaning_ids),
        "mnemonics": _id_list_to_json(mnemonic_ids),
        "plural": None,
        "phrases": _id_list_to_json(phrase_ids),
        "structure": None,
        "third_person": None,
        "present_participle": None,
        "past_tense": None,
        "past_participle": None,
        "base": None,
        "comparative": None,
        "superlative": None,
        "derivatives": None,
        "morpheme_derivatives": None,
        "family": None,
        "source": pg.get("source"),
        "status": 1,
    }
```

- [ ] **Step 4: 跑测试确认 PASS**

Run: `DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test' .venv/bin/python -m pytest tests/replicate/test_field_mapping.py -v`
Expected: 5 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/replicate/field_mapping.py tests/replicate/test_field_mapping.py
git commit -m "feat(replicate): row_to_mysql_word pure function + TDD"
```

---

## Task 4: field_mapping.py — meaning / sentence / mnemonic / phrase

**Files:**
- Modify: `scripts/replicate/field_mapping.py`
- Modify: `tests/replicate/test_field_mapping.py`

- [ ] **Step 1: 追加 meaning 测试**

在 `tests/replicate/test_field_mapping.py` 末尾追加:

```python
from scripts.replicate.field_mapping import row_to_mysql_meaning


def test_meaning_pos_direct_passthrough():
    """wordforge _POS_MAP 已按 wiki 枚举,镜像直传不重映射."""
    pg = {
        "meaning_id": 200001, "word_id": 100001, "pos": 3,  # 3=adj per wiki
        "cn_paraphrase": "的", "en_paraphrase": "adj",
        "equivalents": ["的", "这个"],  # 已是 JSONB list
        "synonyms": None, "antonyms": None,
        "phonetic_us": None, "audio_us": None, "phonetic_uk": None, "audio_uk": None,
        "source": "pipeline:stages.paraphrase",
    }
    row = row_to_mysql_meaning(pg, sentence_ids=[400001, 400002])
    assert row["meaning_id"] == 200001
    assert row["pos"] == 3
    assert row["pos_sub"] is None  # wiki 临时设置 NULL
    assert row["user_group"] is None
    assert json.loads(row["equivalents"]) == ["的", "这个"]
    assert json.loads(row["sentences"]) == [{"sentence_id": 400001}, {"sentence_id": 400002}]
    assert row["synonyms"] is None
    assert row["source"] == "pipeline:stages.paraphrase"


def test_meaning_empty_sentence_list_null():
    pg = {
        "meaning_id": 200001, "word_id": 100001, "pos": None,
        "cn_paraphrase": "x", "en_paraphrase": None,
        "equivalents": None, "synonyms": None, "antonyms": None,
        "phonetic_us": None, "audio_us": None, "phonetic_uk": None, "audio_uk": None,
        "source": None,
    }
    row = row_to_mysql_meaning(pg, sentence_ids=[])
    assert row["sentences"] is None
```

- [ ] **Step 2: 实现 `row_to_mysql_meaning`**

在 `scripts/replicate/field_mapping.py` 末尾追加:

```python
def row_to_mysql_meaning(pg: dict, *, sentence_ids: list[int]) -> dict:
    """Map one domain.meanings row + its sentence id list."""
    equivalents_raw = pg.get("equivalents")
    equivalents_json = (
        json.dumps(equivalents_raw, ensure_ascii=False, separators=(",", ":"))
        if equivalents_raw else None
    )
    synonyms_raw = pg.get("synonyms")
    synonyms_json = (
        json.dumps(synonyms_raw, ensure_ascii=False, separators=(",", ":"))
        if synonyms_raw else None
    )
    antonyms_raw = pg.get("antonyms")
    antonyms_json = (
        json.dumps(antonyms_raw, ensure_ascii=False, separators=(",", ":"))
        if antonyms_raw else None
    )
    return {
        "meaning_id": pg["meaning_id"],
        "word_id": pg["word_id"],
        "user_group": None,
        "pos": pg.get("pos"),
        "pos_sub": None,
        "equivalents": equivalents_json,
        "synonyms": synonyms_json,
        "antonyms": antonyms_json,
        "phonetic_us": pg.get("phonetic_us"),
        "audio_us": pg.get("audio_us"),
        "phonetic_uk": pg.get("phonetic_uk"),
        "audio_uk": pg.get("audio_uk"),
        "cn_paraphrase": pg.get("cn_paraphrase"),
        "en_paraphrase": pg.get("en_paraphrase"),
        "sentences": _id_list_to_json(sentence_ids, key="sentence_id"),
        "source": pg.get("source"),
    }
```

- [ ] **Step 3: 跑 meaning 测试**

Run: `DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test' .venv/bin/python -m pytest tests/replicate/test_field_mapping.py -v`
Expected: 7 tests PASSED(原 5 + 新 2)

- [ ] **Step 4: 追加 sentence / mnemonic / phrase 测试 + 实现**

在 `tests/replicate/test_field_mapping.py` 末尾追加:

```python
from scripts.replicate.field_mapping import (
    row_to_mysql_mnemonic,
    row_to_mysql_phrase,
    row_to_mysql_sentence,
)


def test_sentence_direct_passthrough_audio_nulled():
    """audio_us / audio_uk 按 wiki 临时设置 NULL."""
    pg = {
        "sentence_id": 400001, "word_id": 100001, "meaning_id": 200001,
        "form": "Six of the 38 people were U.S. citizens.",
        "translation": "那 38 人中有 6 个是美国公民.",
        "highlight": [[4, 7]],  # 可能来自 wordforge
        "source": "pipeline:stages.examples",
    }
    row = row_to_mysql_sentence(pg)
    assert row["sentence_id"] == 400001
    assert row["form"].startswith("Six")
    assert row["audio_us"] is None
    assert row["audio_uk"] is None
    assert row["user_group"] is None
    assert row["citation"] is None
    assert row["citation_detail"] is None
    assert json.loads(row["highlight"]) == [[4, 7]]


def test_sentence_highlight_none_stays_none():
    pg = {
        "sentence_id": 400001, "word_id": 100001, "meaning_id": 200001,
        "form": "x", "translation": "y", "highlight": None, "source": None,
    }
    row = row_to_mysql_sentence(pg)
    assert row["highlight"] is None


def test_mnemonic_content_jsonb_passthrough():
    """wordforge mnemonic.content 是 JSONB,直接 json.dumps;creator_id=0 占位."""
    pg = {
        "mnemonic_id": 500001, "word_id": 100001, "type": 1,
        "content": {"kind": "phonetic", "text": "因为在里面,所以说 in."},
        "source": "LLM:claude_sonnet_4_5_thinking",
    }
    row = row_to_mysql_mnemonic(pg)
    assert row["mnemonic_id"] == 500001
    assert row["type"] == 1
    assert row["user_group"] == 0
    assert row["creator_id"] == 0
    assert row["source"] == "LLM:claude_sonnet_4_5_thinking"
    parsed = json.loads(row["content"])
    assert parsed == {"kind": "phonetic", "text": "因为在里面,所以说 in."}


def test_phrase_direct_passthrough():
    pg = {
        "phrase_id": 600001,
        "form": "take off",
        "meaning": "起飞; 脱下",
        "audio_us": "https://cdn/us/take-off.mp3",
        "audio_uk": "https://cdn/uk/take-off.mp3",
    }
    row = row_to_mysql_phrase(pg)
    assert row["phrase_id"] == 600001
    assert row["form"] == "take off"
    assert row["meaning"].startswith("起飞")


def test_phrase_missing_audio_empty_string():
    """wiki: phrase.audio_us / audio_uk NOT NULL.若 PG 无,填 ''."""
    pg = {"phrase_id": 600001, "form": "x", "meaning": "y", "audio_us": None, "audio_uk": None}
    row = row_to_mysql_phrase(pg)
    assert row["audio_us"] == ""
    assert row["audio_uk"] == ""
```

在 `scripts/replicate/field_mapping.py` 末尾追加:

```python
def row_to_mysql_sentence(pg: dict) -> dict:
    highlight = pg.get("highlight")
    highlight_json = (
        json.dumps(highlight, separators=(",", ":")) if highlight else None
    )
    return {
        "sentence_id": pg["sentence_id"],
        "word_id": pg["word_id"],
        "meaning_id": pg["meaning_id"],
        "user_group": None,
        "form": pg.get("form"),
        "highlight": highlight_json,
        "translation": pg["translation"],
        "audio_us": None,
        "audio_uk": None,
        "source": pg.get("source"),
        "citation": None,
        "citation_detail": None,
    }


def row_to_mysql_mnemonic(pg: dict) -> dict:
    content = pg["content"]
    content_json = (
        json.dumps(content, ensure_ascii=False, separators=(",", ":"))
        if isinstance(content, dict) else str(content)
    )
    return {
        "mnemonic_id": pg["mnemonic_id"],
        "word_id": pg["word_id"],
        "type": pg["type"],
        "user_group": 0,
        "content": content_json,
        "source": pg.get("source"),
        "creator_id": 0,
    }


def row_to_mysql_phrase(pg: dict) -> dict:
    return {
        "phrase_id": pg["phrase_id"],
        "form": pg["form"],
        "meaning": pg["meaning"],
        "audio_us": pg.get("audio_us") or "",
        "audio_uk": pg.get("audio_uk") or "",
    }
```

- [ ] **Step 5: 跑全部 field_mapping 测试**

Run: `DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test' .venv/bin/python -m pytest tests/replicate/test_field_mapping.py -v`
Expected: 12 tests PASSED

- [ ] **Step 6: ruff**

Run: `.venv/bin/ruff check scripts/replicate/ tests/replicate/`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add scripts/replicate/field_mapping.py tests/replicate/test_field_mapping.py
git commit -m "feat(replicate): meaning/sentence/mnemonic/phrase row mappings"
```

---

## Task 5: mirror_to_mysql.py 骨架 + CLI + env 校验

**Files:**
- Create: `scripts/replicate/mirror_to_mysql.py`

- [ ] **Step 1: 写脚本文件头 + CLI + 环境校验**

创建 `scripts/replicate/mirror_to_mysql.py`:

```python
"""PG domain.* -> MySQL word_forge mirror (one-shot).

Spec: docs/superpowers/specs/2026-05-02-dual-write-mysql-design.md
Plan: docs/superpowers/plans/2026-05-03-dual-write-mysql.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

# ruff: noqa: E501

TABLES = ["word", "meaning", "sentence", "mnemonic", "phrase"]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit(
            f"ERROR: missing env vars: {missing}.\n"
            f"  source ~/.wordforge/prod.env\n"
            f"  source ~/.wordforge/mysql_writer.env"
        )
    return {n: os.environ[n] for n in names}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Mirror wordforge domain.* -> MySQL word_forge.* "
        "via shadow tables + atomic RENAME swap.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Go through stage 0-2 (SELECT + build + INSERT into _shadow) "
        "but skip stage 3 (RENAME swap). Safe against live gozero reads.",
    )
    p.add_argument(
        "--run-log", type=Path, default=Path("./replicate_run.jsonl"),
        help="Where to append per-run sanity records.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    env = _require_env("DATABASE_URL", "WORDFORGE_MYSQL_WRITER_DSN")
    _log(f"mode={'DRY-RUN (no RENAME)' if args.dry_run else 'LIVE'}")
    pg = create_engine(env["DATABASE_URL"], future=True)
    my = create_engine(env["WORDFORGE_MYSQL_WRITER_DSN"], future=True, pool_recycle=1800)
    try:
        _stage0_sanity(pg, my)
        _stage1_truncate_shadow(my)
        counts = _stage2_load_shadow(pg, my)
        if not args.dry_run:
            _stage3_swap(my)
        mismatches = _stage4_count_check(pg, my, counts, dry_run=args.dry_run)
        _stage5_summary(counts, mismatches, args.run_log, dry_run=args.dry_run)
        return 0
    finally:
        pg.dispose()
        my.dispose()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 加占位 stage 函数让模块可 import**

在 `main()` 之前追加 5 个占位(下面 task 实现):

```python
def _stage0_sanity(pg_engine, my_engine) -> None:
    raise NotImplementedError("Task 6")


def _stage1_truncate_shadow(my_engine) -> None:
    raise NotImplementedError("Task 6")


def _stage2_load_shadow(pg_engine, my_engine) -> dict[str, int]:
    raise NotImplementedError("Task 7")


def _stage3_swap(my_engine) -> None:
    raise NotImplementedError("Task 8")


def _stage4_count_check(pg_engine, my_engine, counts: dict[str, int], *, dry_run: bool) -> list[str]:
    raise NotImplementedError("Task 9")


def _stage5_summary(counts: dict[str, int], mismatches: list[str], run_log: Path, *, dry_run: bool) -> None:
    raise NotImplementedError("Task 9")
```

- [ ] **Step 3: 冒烟 — `--help` 和 env 缺失都按预期**

Run: `.venv/bin/python scripts/replicate/mirror_to_mysql.py --help`
Expected: argparse 打印 usage,含 `--dry-run` / `--run-log`。

Run: `unset DATABASE_URL WORDFORGE_MYSQL_WRITER_DSN && .venv/bin/python scripts/replicate/mirror_to_mysql.py`
Expected: `ERROR: missing env vars: [...]` + 两行 source 提示,exit 1。

- [ ] **Step 4: ruff**

Run: `.venv/bin/ruff check scripts/replicate/mirror_to_mysql.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add scripts/replicate/mirror_to_mysql.py
git commit -m "feat(replicate): mirror_to_mysql skeleton + CLI + env guard"
```

---

## Task 6: stage 0 (sanity) + stage 1 (TRUNCATE shadow)

**Files:**
- Modify: `scripts/replicate/mirror_to_mysql.py`

- [ ] **Step 1: 实现 `_stage0_sanity`**

替换 Task 5 里 `_stage0_sanity` 的 `raise NotImplementedError`:

```python
def _stage0_sanity(pg_engine, my_engine) -> None:
    """Verify every expected table + shadow exists; drop stale *_old if any."""
    _log("stage 0: sanity check PG + MySQL tables ...")
    with pg_engine.connect() as conn:
        for t in TABLES:
            pg_t = f"domain.{t}s" if t != "phrase" else "domain.phrases"
            # domain.words / meanings / sentences / mnemonics / phrases
            conn.execute(text(f"SELECT 1 FROM {pg_t} LIMIT 0"))
    _log("  PG domain.* tables OK")

    with my_engine.begin() as conn:
        # main + shadow must exist
        for t in TABLES:
            for name in (t, f"{t}_shadow"):
                conn.execute(text(f"SELECT 1 FROM {name} LIMIT 0"))
        # clean up *_old leftovers from a crashed previous run
        rows = conn.execute(text("SHOW TABLES LIKE '%_old'")).all()
        for (name,) in rows:
            _log(f"  dropping stale {name}")
            conn.execute(text(f"DROP TABLE `{name}`"))
    _log("  MySQL main + shadow tables OK")
```

- [ ] **Step 2: 实现 `_stage1_truncate_shadow`**

```python
def _stage1_truncate_shadow(my_engine) -> None:
    _log("stage 1: TRUNCATE shadow tables ...")
    with my_engine.begin() as conn:
        for t in TABLES:
            conn.execute(text(f"TRUNCATE TABLE `{t}_shadow`"))
    _log("  shadows cleared")
```

- [ ] **Step 3: 本地冒烟 stage 0(需要 DB 已就绪)**

前提:Task 11 的 manual playbook 已跑过一次(DB + schema + 账号 + 凭证到位)。

```bash
source ~/.wordforge/prod.env
source ~/.wordforge/mysql_writer.env
.venv/bin/python -c "
from scripts.replicate.mirror_to_mysql import _stage0_sanity, _stage1_truncate_shadow
from sqlalchemy import create_engine
import os
pg = create_engine(os.environ['DATABASE_URL'], future=True)
my = create_engine(os.environ['WORDFORGE_MYSQL_WRITER_DSN'], future=True)
_stage0_sanity(pg, my)
_stage1_truncate_shadow(my)
print('stage 0 + 1 OK')
"
```

Expected: 两个 `stage ...` 日志 + `stage 0 + 1 OK`。

如果这步报 "table doesn't exist",说明 init_database.sql + mysql_schema.sql 还没应用,回去看 Task 11 playbook。**不要在这里自动建表**。

- [ ] **Step 4: ruff**

Run: `.venv/bin/ruff check scripts/replicate/mirror_to_mysql.py`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add scripts/replicate/mirror_to_mysql.py
git commit -m "feat(replicate): stage 0 sanity + stage 1 truncate-shadow"
```

---

## Task 7: stage 2 — 读 PG + 灌 MySQL shadow

这是全脚本最核心的一步。分两步实现:先构造 "word_id -> children id list" 的
三张 map(用于 word 表的 meanings/mnemonics/phrases JSON 字段),再按表
stream 读 + 5000 batch INSERT shadow。

**Files:**
- Modify: `scripts/replicate/mirror_to_mysql.py`

- [ ] **Step 1: 加 helper `_load_relations_map` — 一次 SELECT 构造三张 id 表**

在 `_stage1_truncate_shadow` 下面追加:

```python
def _load_relations_map(pg_engine) -> dict[str, dict[int, list[int]]]:
    """Build three word_id -> [child_id,...] maps for word table's JSON cols.

    Single SELECT per child table, ORDER BY child_id ASC so the resulting
    JSON arrays are stable across runs (helps checksum parity).
    """
    _log("  loading relation maps (word -> meanings/mnemonics/phrases) ...")
    out: dict[str, dict[int, list[int]]] = {"meanings": {}, "mnemonics": {}, "phrases": {}}
    with pg_engine.connect() as conn:
        for kind, sql in (
            ("meanings", "SELECT word_id, meaning_id FROM domain.meanings ORDER BY meaning_id"),
            ("mnemonics", "SELECT word_id, mnemonic_id FROM domain.mnemonics ORDER BY mnemonic_id"),
            ("phrases", "SELECT owner_word_id AS word_id, phrase_id FROM domain.phrases ORDER BY phrase_id"),
        ):
            for word_id, child_id in conn.execute(text(sql)):
                out[kind].setdefault(word_id, []).append(child_id)
    _log(f"    meanings map: {len(out['meanings'])} words; "
         f"mnemonics map: {len(out['mnemonics'])} words; "
         f"phrases map: {len(out['phrases'])} words")
    return out


def _load_sentence_map(pg_engine) -> dict[int, list[int]]:
    """meaning_id -> [sentence_id,...], for meaning table's sentences JSON col."""
    _log("  loading relation map (meaning -> sentences) ...")
    out: dict[int, list[int]] = {}
    with pg_engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT meaning_id, sentence_id FROM domain.sentences ORDER BY sentence_id"
        ))
        for meaning_id, sentence_id in rows:
            out.setdefault(meaning_id, []).append(sentence_id)
    _log(f"    sentence map: {len(out)} meanings")
    return out
```

- [ ] **Step 2: 加 helper `_stream_and_insert` — 按表批量灌 shadow**

在 `_load_sentence_map` 下面追加:

```python
def _stream_and_insert(
    pg_engine,
    my_engine,
    *,
    table: str,
    select_sql: str,
    row_to_mysql,
    insert_sql: str,
    batch_size: int = 5000,
) -> int:
    """Stream rows from PG, map each, INSERT to MySQL shadow in batches."""
    _log(f"  loading {table} ...")
    total = 0
    batch: list[dict] = []
    with pg_engine.connect().execution_options(stream_results=True, yield_per=batch_size) as pg_conn:
        pg_rows = pg_conn.execute(text(select_sql))
        columns = pg_rows.keys()
        with my_engine.begin() as my_conn:
            for pg_row in pg_rows:
                mapped = row_to_mysql(dict(zip(columns, pg_row, strict=True)))
                batch.append(mapped)
                if len(batch) >= batch_size:
                    my_conn.execute(text(insert_sql), batch)
                    total += len(batch)
                    _log(f"    {table}: {total} rows inserted")
                    batch.clear()
            if batch:
                my_conn.execute(text(insert_sql), batch)
                total += len(batch)
    _log(f"  {table}: {total} total")
    return total
```

- [ ] **Step 3: ruff**

Run: `.venv/bin/ruff check scripts/replicate/mirror_to_mysql.py`
Expected: `All checks passed!`

- [ ] **Step 4: Commit(不 commit 没用的 stage 2 本体,下 step 和它一起 commit)**

Skip — 合到 Step 7 最后 commit.

- [ ] **Step 5: 实现 `_stage2_load_shadow` — 把 5 张表串起来**

替换 Task 5 的 `_stage2_load_shadow` 占位:

```python
# 放在 mirror_to_mysql.py 顶部 imports 下面
from scripts.replicate.field_mapping import (
    row_to_mysql_meaning,
    row_to_mysql_mnemonic,
    row_to_mysql_phrase,
    row_to_mysql_sentence,
    row_to_mysql_word,
)

_WORD_COLS = "word_id, type, form, phonetic_us, audio_us, phonetic_uk, audio_uk, source"
_MEANING_COLS = (
    "meaning_id, word_id, pos, equivalents, synonyms, antonyms, "
    "phonetic_us, audio_us, phonetic_uk, audio_uk, "
    "cn_paraphrase, en_paraphrase, source"
)
_SENTENCE_COLS = (
    "sentence_id, word_id, meaning_id, form, translation, highlight, source"
)
_MNEMONIC_COLS = "mnemonic_id, word_id, type, content, source"
_PHRASE_COLS = "phrase_id, owner_word_id, form, meaning"


def _insert_sql(table: str, cols: list[str]) -> str:
    placeholders = ", ".join(f":{c}" for c in cols)
    collist = ", ".join(f"`{c}`" for c in cols)
    return f"INSERT INTO `{table}_shadow` ({collist}) VALUES ({placeholders})"


def _stage2_load_shadow(pg_engine, my_engine) -> dict[str, int]:
    _log("stage 2: PG -> MySQL shadow ...")
    counts: dict[str, int] = {}

    rel = _load_relations_map(pg_engine)
    sent_map = _load_sentence_map(pg_engine)

    # 1. word: map needs meaning/mnemonic/phrase id lists keyed by word_id
    word_cols_out = [
        "word_id", "type", "form", "phonetic_us", "audio_us", "phonetic_uk", "audio_uk",
        "meanings", "mnemonics", "plural", "phrases", "structure",
        "third_person", "present_participle", "past_tense", "past_participle",
        "base", "comparative", "superlative", "derivatives", "morpheme_derivatives",
        "family", "source", "status",
    ]
    def _w(pg_row: dict) -> dict:
        w = pg_row["word_id"]
        return row_to_mysql_word(
            pg_row,
            meaning_ids=rel["meanings"].get(w, []),
            mnemonic_ids=rel["mnemonics"].get(w, []),
            phrase_ids=rel["phrases"].get(w, []),
        )
    counts["word"] = _stream_and_insert(
        pg_engine, my_engine,
        table="word",
        select_sql=f"SELECT {_WORD_COLS} FROM domain.words ORDER BY word_id",
        row_to_mysql=_w,
        insert_sql=_insert_sql("word", word_cols_out),
    )

    # 2. meaning
    meaning_cols_out = [
        "meaning_id", "word_id", "user_group", "pos", "pos_sub",
        "equivalents", "synonyms", "antonyms",
        "phonetic_us", "audio_us", "phonetic_uk", "audio_uk",
        "cn_paraphrase", "en_paraphrase", "sentences", "source",
    ]
    def _m(pg_row: dict) -> dict:
        return row_to_mysql_meaning(
            pg_row, sentence_ids=sent_map.get(pg_row["meaning_id"], []),
        )
    counts["meaning"] = _stream_and_insert(
        pg_engine, my_engine,
        table="meaning",
        select_sql=f"SELECT {_MEANING_COLS} FROM domain.meanings ORDER BY meaning_id",
        row_to_mysql=_m,
        insert_sql=_insert_sql("meaning", meaning_cols_out),
    )

    # 3. sentence
    sentence_cols_out = [
        "sentence_id", "word_id", "meaning_id", "user_group",
        "form", "highlight", "translation",
        "audio_us", "audio_uk", "source", "citation", "citation_detail",
    ]
    counts["sentence"] = _stream_and_insert(
        pg_engine, my_engine,
        table="sentence",
        select_sql=f"SELECT {_SENTENCE_COLS} FROM domain.sentences ORDER BY sentence_id",
        row_to_mysql=row_to_mysql_sentence,
        insert_sql=_insert_sql("sentence", sentence_cols_out),
    )

    # 4. mnemonic
    mnemonic_cols_out = [
        "mnemonic_id", "word_id", "type", "user_group", "content", "source", "creator_id",
    ]
    counts["mnemonic"] = _stream_and_insert(
        pg_engine, my_engine,
        table="mnemonic",
        select_sql=f"SELECT {_MNEMONIC_COLS} FROM domain.mnemonics ORDER BY mnemonic_id",
        row_to_mysql=row_to_mysql_mnemonic,
        insert_sql=_insert_sql("mnemonic", mnemonic_cols_out),
    )

    # 5. phrase — wordforge 0 行,跳过(select 仍然跑一遍作为 sanity)
    phrase_cols_out = ["phrase_id", "form", "meaning", "audio_us", "audio_uk"]
    def _p(pg_row: dict) -> dict:
        # domain.phrases 用 owner_word_id,我们 SELECT 时 alias 成 word_id
        # 但 MySQL phrase 表没有 word_id;忽略即可(row_to_mysql_phrase 不读 word_id)
        return row_to_mysql_phrase(pg_row)
    counts["phrase"] = _stream_and_insert(
        pg_engine, my_engine,
        table="phrase",
        select_sql=f"SELECT {_PHRASE_COLS} FROM domain.phrases ORDER BY phrase_id",
        row_to_mysql=_p,
        insert_sql=_insert_sql("phrase", phrase_cols_out),
    )

    return counts
```

- [ ] **Step 6: dry-run 冒烟(stage 0-2 全跑,不 RENAME)**

```bash
source ~/.wordforge/prod.env
source ~/.wordforge/mysql_writer.env
.venv/bin/python scripts/replicate/mirror_to_mysql.py --dry-run 2>&1 | tail -30
```

Expected(近似):
- `stage 0: sanity ... OK`
- `stage 1: TRUNCATE ... cleared`
- `stage 2: ... word: 121057 total / meaning: 241763 total / sentence: 534033 total / mnemonic: 121057 total / phrase: 0 total`
- 不跑 stage 3,shadow 表被灌满。手动 `SELECT COUNT(*) FROM word_shadow;` 应等于 121057。

如果报 "column doesn't exist" 或 MySQL `Data too long` 等:停下来看 field_mapping 有没有需要截断 / NULL 处理的边界。

- [ ] **Step 7: ruff + pytest**

```bash
.venv/bin/ruff check scripts/replicate/ tests/replicate/
DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test' .venv/bin/python -m pytest tests/replicate/ -v
```
Expected: ruff All checks passed!;pytest 12 passed。

- [ ] **Step 8: Commit**

```bash
git add scripts/replicate/mirror_to_mysql.py
git commit -m "feat(replicate): stage 2 stream PG to MySQL shadow tables"
```

---

## Task 8: stage 3 — 原子 RENAME swap

**Files:**
- Modify: `scripts/replicate/mirror_to_mysql.py`

- [ ] **Step 1: 实现 `_stage3_swap`(两条 RENAME 语句)**

替换占位:

```python
def _stage3_swap(my_engine) -> None:
    """Atomic shadow swap per spec §6 stage 3.

    Statement A: main -> _old, shadow -> main (single atomic RENAME).
    Statement B: _old -> _shadow (frees slot for next run).
    """
    _log("stage 3: atomic RENAME swap ...")
    stmt_a = ",".join(
        f" `{t}` TO `{t}_old`, `{t}_shadow` TO `{t}`"
        for t in TABLES
    )
    stmt_b = ",".join(
        f" `{t}_old` TO `{t}_shadow`"
        for t in TABLES
    )
    with my_engine.begin() as conn:
        conn.execute(text(f"RENAME TABLE{stmt_a}"))
        _log("  statement A committed (main+shadow swapped)")
        conn.execute(text(f"RENAME TABLE{stmt_b}"))
        _log("  statement B committed (_old -> _shadow)")
```

- [ ] **Step 2: ruff**

Run: `.venv/bin/ruff check scripts/replicate/mirror_to_mysql.py`
Expected: All checks passed!

- [ ] **Step 3: 手动测 RENAME 逻辑(live 跑之前先验证语句拼接)**

Run:

```bash
.venv/bin/python -c "
from scripts.replicate.mirror_to_mysql import TABLES
stmt_a = ','.join(f' \`{t}\` TO \`{t}_old\`, \`{t}_shadow\` TO \`{t}\`' for t in TABLES)
stmt_b = ','.join(f' \`{t}_old\` TO \`{t}_shadow\`' for t in TABLES)
print('A:', 'RENAME TABLE' + stmt_a)
print('B:', 'RENAME TABLE' + stmt_b)
"
```

Expected: 两条合法的 MySQL RENAME TABLE 语句,A 含 10 个 rename 子句(5 表 × 2 方向),B 含 5 个。

- [ ] **Step 4: Commit**

```bash
git add scripts/replicate/mirror_to_mysql.py
git commit -m "feat(replicate): stage 3 atomic shadow RENAME swap"
```

---

## Task 9: stage 4-5 — count check + summary

**Files:**
- Modify: `scripts/replicate/mirror_to_mysql.py`

- [ ] **Step 1: 实现 `_stage4_count_check`**

替换占位:

```python
def _stage4_count_check(
    pg_engine, my_engine, counts: dict[str, int], *, dry_run: bool
) -> list[str]:
    """Compare PG domain.* vs MySQL main (post-swap).

    In dry-run we compare shadow instead of main, since swap didn't happen.
    """
    _log("stage 4: count check ...")
    mismatches: list[str] = []
    target_suffix = "_shadow" if dry_run else ""
    with pg_engine.connect() as pg_conn, my_engine.connect() as my_conn:
        for t in TABLES:
            pg_table = f"domain.{t}s" if t != "phrase" else "domain.phrases"
            pg_n = pg_conn.execute(text(f"SELECT count(*) FROM {pg_table}")).scalar_one()
            my_n = my_conn.execute(
                text(f"SELECT count(*) FROM `{t}{target_suffix}`")
            ).scalar_one()
            if pg_n != my_n:
                msg = f"{t}: PG={pg_n} MySQL={my_n} (loaded={counts.get(t)})"
                _log(f"  MISMATCH {msg}")
                mismatches.append(msg)
            else:
                _log(f"  OK {t}: {pg_n}")
    return mismatches
```

- [ ] **Step 2: 实现 `_stage5_summary`**

```python
def _stage5_summary(
    counts: dict[str, int],
    mismatches: list[str],
    run_log: Path,
    *,
    dry_run: bool,
) -> None:
    _log(f"stage 5: summary counts={counts} mismatches={len(mismatches)}")
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "dry_run": dry_run,
        "counts": counts,
        "mismatches": mismatches,
    }
    with run_log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    if mismatches:
        print(f"WARN drift detected: {mismatches}", file=sys.stderr, flush=True)
```

- [ ] **Step 3: ruff**

Run: `.venv/bin/ruff check scripts/replicate/mirror_to_mysql.py`
Expected: All checks passed!

- [ ] **Step 4: Commit**

```bash
git add scripts/replicate/mirror_to_mysql.py
git commit -m "feat(replicate): stage 4 count check + stage 5 summary"
```

---

## Task 10: 初始化 + 首次实跑(手工验证,不写代码)

**Files:** 无代码改动,纯 ops playbook 演练,用于验证 Task 1-9 在真实 MySQL 上可跑。

- [ ] **Step 1: 准备 init_database.sql + 建库 + 建账号**

```bash
cp scripts/replicate/init_database.sql.example scripts/replicate/init_database.sql
# 用编辑器把 <pwd-writer> / <pwd-reader> 替换为:
#   openssl rand -base64 24 | tr -d '='
# (两个密码分别生成,写下来)
# init_database.sql 已被 .gitignore,不进仓

mysql -h 120.27.242.42 -P 3306 -u root -p < scripts/replicate/init_database.sql
```

Expected: `Query OK` 一系列,不报错。

- [ ] **Step 2: 建表**

```bash
mysql -h 120.27.242.42 -P 3306 -u wordforge_writer -p word_forge \
  < scripts/replicate/mysql_schema.sql
```

Expected: 10 个 `Query OK`(5 主表 + 5 shadow)。

验证:

```bash
mysql -h 120.27.242.42 -u wordforge_writer -p word_forge -e "SHOW TABLES"
```
Expected: 10 行表名。

- [ ] **Step 3: 凭证文件**

```bash
umask 077
cat > ~/.wordforge/mysql_writer.env <<'EOF'
export WORDFORGE_MYSQL_WRITER_DSN='mysql+pymysql://wordforge_writer:<pwd-writer>@120.27.242.42:3306/word_forge?charset=utf8mb4'
EOF
cat > ~/.wordforge/mysql_reader.env <<'EOF'
export WORDFORGE_MYSQL_READER_DSN='mysql+pymysql://wordforge_reader:<pwd-reader>@120.27.242.42:3306/word_forge?charset=utf8mb4'
EOF
chmod 600 ~/.wordforge/mysql_{writer,reader}.env
```

把 `<pwd-*>` 替换成 Step 1 生成的密码。

- [ ] **Step 4: dry-run 全量**

```bash
source ~/.wordforge/prod.env
source ~/.wordforge/mysql_writer.env
.venv/bin/python scripts/replicate/mirror_to_mysql.py --dry-run 2>&1 | tee /tmp/replicate_dry.log
```

Expected: 最后 `stage 5: summary counts={'word': 121057, 'meaning': 241763, 'sentence': 534033, 'mnemonic': 121057, 'phrase': 0}`,mismatches=0。

如果 `_shadow` 表数量和 PG 不一致,停下查原因(可能字段映射 bug、or MySQL TRUNCATE 没干净)。

- [ ] **Step 5: live 跑(要你口头确认)**

⚠️ 这一步真会 swap 读路径。前提:gozero 后端还没指向新库(否则切换瞬间它开始读)。

```bash
.venv/bin/python scripts/replicate/mirror_to_mysql.py 2>&1 | tee /tmp/replicate_live.log
```

Expected: 同 Step 4,stage 3 多两条 `statement A/B committed`。

- [ ] **Step 6: post-check**

```bash
mysql -h 120.27.242.42 -u wordforge_reader -p word_forge -e \
  "SELECT (SELECT COUNT(*) FROM word) AS w, \
          (SELECT COUNT(*) FROM meaning) AS m, \
          (SELECT COUNT(*) FROM sentence) AS s, \
          (SELECT COUNT(*) FROM mnemonic) AS n, \
          (SELECT COUNT(*) FROM phrase) AS p;"
```

Expected: `w=121057 m=241763 s=534033 n=121057 p=0`(数字以当时 PG 实际为准)。

抽查一行 json 字段正确:

```bash
mysql -h 120.27.242.42 -u wordforge_reader -p word_forge -e \
  "SELECT word_id, form, meanings FROM word WHERE word_id IN (100001, 100003) LIMIT 2\G"
```
Expected: `meanings` 列是 `[{"id":N}]` JSON 字符串。

---

## Task 11: verify_mysql_mirror.py 对账脚本

**Files:**
- Create: `scripts/replicate/verify_mysql_mirror.py`

- [ ] **Step 1: 写脚本**

```python
"""PG vs MySQL count + checksum drift verifier.

Spec §7.2.  Prints a one-row summary per table; exits non-zero if any
count mismatches (checksum diffs are reported but not fatal because the
two sides use different hash functions).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy import create_engine, text

# ruff: noqa: E501

TABLES = [
    # (name, pg_table, key_cols_concat_expr_pg, key_cols_concat_expr_mysql)
    ("word",     "domain.words",     "word_id || '|' || form || '|' || type || '|' || coalesce(source, '')",
                                    "CONCAT_WS('|', word_id, form, type, IFNULL(source, ''))"),
    ("meaning",  "domain.meanings",  "meaning_id || '|' || word_id || '|' || coalesce(pos::text, '') || '|' || coalesce(cn_paraphrase, '')",
                                    "CONCAT_WS('|', meaning_id, word_id, IFNULL(pos, ''), IFNULL(cn_paraphrase, ''))"),
    ("sentence", "domain.sentences", "sentence_id || '|' || word_id || '|' || coalesce(form, '')",
                                    "CONCAT_WS('|', sentence_id, word_id, IFNULL(form, ''))"),
    ("mnemonic", "domain.mnemonics", "mnemonic_id || '|' || word_id || '|' || type::text",
                                    "CONCAT_WS('|', mnemonic_id, word_id, type)"),
    ("phrase",   "domain.phrases",   "phrase_id || '|' || form",
                                    "CONCAT_WS('|', phrase_id, form)"),
]


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--report", type=Path, default=Path("./drift_report.jsonl"))
    args = p.parse_args()
    pg_url = os.environ.get("DATABASE_URL")
    my_url = os.environ.get("WORDFORGE_MYSQL_READER_DSN")
    if not pg_url or not my_url:
        sys.exit("ERROR: need DATABASE_URL + WORDFORGE_MYSQL_READER_DSN")
    pg = create_engine(pg_url, future=True)
    my = create_engine(my_url, future=True)
    has_drift = False
    records = []
    try:
        with pg.connect() as pgc, my.connect() as myc:
            for name, pg_table, pg_concat, my_concat in TABLES:
                pg_n = pgc.execute(text(f"SELECT count(*) FROM {pg_table}")).scalar_one()
                my_n = myc.execute(text(f"SELECT count(*) FROM `{name}`")).scalar_one()
                pg_sum = pgc.execute(text(
                    f"SELECT md5(string_agg({pg_concat}, ',' ORDER BY 1)) FROM {pg_table}"
                )).scalar_one() if pg_n else None
                my_sum = myc.execute(text(
                    f"SELECT HEX(BIT_XOR(CAST(CRC32({my_concat}) AS UNSIGNED))) FROM `{name}`"
                )).scalar_one() if my_n else None
                drift = pg_n != my_n
                status = "DRIFT" if drift else "OK"
                _log(f"  {status} {name}: pg={pg_n} mysql={my_n} pg_md5={pg_sum} my_xor_crc32={my_sum}")
                records.append({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "table": name,
                    "pg_count": pg_n, "mysql_count": my_n,
                    "pg_md5": pg_sum, "mysql_xor_crc32": my_sum,
                    "drift": drift,
                })
                if drift:
                    has_drift = True
    finally:
        pg.dispose()
        my.dispose()

    with args.report.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if has_drift:
        print("DRIFT: count mismatch found, see drift_report.jsonl", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: ruff**

Run: `.venv/bin/ruff check scripts/replicate/verify_mysql_mirror.py`
Expected: All checks passed!

- [ ] **Step 3: 冒烟跑**

```bash
source ~/.wordforge/prod.env
source ~/.wordforge/mysql_reader.env
.venv/bin/python scripts/replicate/verify_mysql_mirror.py 2>&1 | tail -10
```

Expected: 5 行 `OK <table>: pg=N mysql=N pg_md5=<hex> my_xor_crc32=<hex>`,exit 0。

**注意**:md5(PG)和 xor_crc32(MySQL) 两个哈希函数不同,两边值**不会相等**。
只校 count 作为硬条件;checksum 记下来供人工 baseline 比较(下一轮同侧值应一致)。

- [ ] **Step 4: Commit**

```bash
git add scripts/replicate/verify_mysql_mirror.py
git commit -m "feat(replicate): verify_mysql_mirror count + checksum drift check"
```

---

## Self-Review

### Spec coverage 检查表

| Spec 章节 | Task 覆盖 |
|---|---|
| §1 做 / 不做 | Task 1-11 范围内,未改 pipeline / 未上定时 |
| §2 数据源 + 凭证 | Task 10 Step 1-3 建库 + 凭证;.env 文件名与 spec 一致 |
| §3.1 初始化账号 | Task 2 (example) + Task 10 Step 1 (实跑) |
| §3.2 建表 | Task 2 (DDL) + Task 10 Step 2 (实跑) |
| §3.3 凭证 | Task 10 Step 3 |
| §4 MySQL schema | Task 2 `mysql_schema.sql` |
| §5.1-5.5 字段映射 | Task 3-4 5 个纯函数 |
| §6 stage 0-5 流程 | Task 5(骨架) + Task 6(stage 0-1) + Task 7(stage 2) + Task 8(stage 3) + Task 9(stage 4-5) |
| §7 对账 | Task 9(内层 count) + Task 11(外层脚本) |
| §8 CLI | Task 5 `--dry-run` + Task 11 `--report` |
| §9 错误处理 | 脚本分散处理(env/表不存在/RENAME 失败分别在 stage 0 / stage 3)|
| §10 playbook | Task 10 Step 1-6 |
| §11 测试 | Task 3-4 pytest 12 用例 + Task 7 Step 6 dry-run 冒烟 |
| §12 风险 | Task 6 Step 1 drop stale *_old 清理(针对语句 B 失败残留) |
| §13 非目标 | 全程未涉及 pipeline / 定时 / CDC |

### Placeholder 扫描

无 TBD / TODO / "fill in details"。所有代码块是完整可跑代码,所有命令有 Expected 描述。

### 类型 / 命名一致性

- `row_to_mysql_word / meaning / sentence / mnemonic / phrase`:签名一致,返回 `dict`
- `_stage0_sanity / _stage1_truncate_shadow / _stage2_load_shadow / _stage3_swap / _stage4_count_check / _stage5_summary`:名字和调用点一致
- `TABLES` 常量全脚本共用;`_WORD_COLS / _MEANING_COLS / ...` 只在 stage 2 用
- `WORDFORGE_MYSQL_WRITER_DSN / WORDFORGE_MYSQL_READER_DSN` 两个 env var 名全 spec / plan 一致

---

## Execution Handoff

Plan 已写完,共 11 个 Task,~150 行每 Task。两种执行方式:

1. **Subagent-Driven(推荐)** — 每 Task 派一个 fresh subagent,我在 Task 间 review,节奏最快
2. **Inline** — 在当前 session 顺序执行,每 Task 末给你看结果

你选哪种?



