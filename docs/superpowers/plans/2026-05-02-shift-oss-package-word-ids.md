# OSS package-words word_id shift — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一次性脚本把 OSS bucket `sailing-words-package-words` 里 1443 个 package
object 的 `words[].id` 全部减 `999_900_000`,和 wordforge PG `domain.words.word_id`
的 10 万级空间对齐。

**Architecture:** 单脚本 `scripts/packaging/shift_oss_package_word_ids.py`,纯函数
`transform_body` 做核心 id 转换(便于单测),主流程顺序拉 PG/OSS、全量备份到
`./bak/`、dry-run 默认不写、`--i-am-writing-prod` 才真 `put_object`。幂等靠
`max(id) < 10^9` 自动识别已处理的 package。

**Tech Stack:** Python 3.12 / oss2 2.19.1 / psycopg(已在 `.venv`) / pytest。

**Spec 引用:** `docs/superpowers/specs/2026-05-02-shift-oss-package-word-ids-design.md`

---

## File Structure

| 文件 | 动作 | 职责 |
|---|---|---|
| `scripts/packaging/shift_oss_package_word_ids.py` | 新建 | 一次性脚本:main + transform_body + PG 加载 + OSS IO |
| `tests/packaging/test_shift_oss_package_word_ids.py` | 新建 | `transform_body` 纯函数单测 |
| `.gitignore` | 已改(上一 commit) | 排除 `./bak/` 和 `./oss_shift_dead_letter.jsonl` |

不碰 `scripts/packaging/packager.py` / `builder.py` / `export_sailing_sqlite.py`。

---

## Task 1: transform_body 纯函数 + 单测骨架

建 `transform_body` 的 TDD 循环。核心转换是纯函数,和 OSS/PG 完全解耦。

**Files:**
- Create: `scripts/packaging/shift_oss_package_word_ids.py`
- Create: `tests/packaging/test_shift_oss_package_word_ids.py`

- [ ] **Step 1: 写第一个失败测试 — ok 分支(原始 10 亿级 id 全部合法)**

创建 `tests/packaging/test_shift_oss_package_word_ids.py`:

```python
"""Unit tests for scripts/packaging/shift_oss_package_word_ids.py.

Tests the pure `transform_body` function. No real OSS/PG; fixtures only.
"""

from __future__ import annotations

import json

import pytest

from scripts.packaging.shift_oss_package_word_ids import (
    WORD_ID_OFFSET,
    InvalidMixedIdRangeError,
    transform_body,
)


def _body(words_per_unit: list[list[int]]) -> str:
    """Build a minimal JSON body with the given word ids (flat lists per unit)."""
    return json.dumps(
        [
            {
                "id": 10 + i,
                "title": f"unit {i}",
                "words": [{"id": wid, "weight": 0} for wid in ids],
            }
            for i, ids in enumerate(words_per_unit)
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def test_transform_ok_all_original_ids():
    valid = {100003, 100063, 103995}
    body = _body([[1_000_000_003, 1_000_000_063], [1_000_003_995]])

    new_body, status, details = transform_body(body, valid_new_ids=valid)

    assert status == "ok"
    assert details == {}
    parsed = json.loads(new_body)
    got_ids = [w["id"] for u in parsed for w in u["words"]]
    assert got_ids == [100003, 100063, 103995]
    # unit / title / weight 保持不变
    assert [u["id"] for u in parsed] == [10, 11]
    assert [u["title"] for u in parsed] == ["unit 0", "unit 1"]
    assert all(w["weight"] == 0 for u in parsed for w in u["words"])
```

- [ ] **Step 2: 运行测试确认 FAIL(模块不存在)**

Run: `.venv/bin/python -m pytest tests/packaging/test_shift_oss_package_word_ids.py -v`
Expected: `ModuleNotFoundError: No module named 'scripts.packaging.shift_oss_package_word_ids'`

- [ ] **Step 3: 写最小实现让这个测试过**

创建 `scripts/packaging/shift_oss_package_word_ids.py`:

```python
"""One-shot script: shift OSS package-words word_id by -999_900_000.

Spec: docs/superpowers/specs/2026-05-02-shift-oss-package-word-ids-design.md
"""

from __future__ import annotations

import json

# ruff: noqa: E501 — long prompt/log strings for readability

# 和 scripts/mirror_momo_packages.py::WORDFORGE_WORD_ID_SHIFT /
# words_core/scripts/migrate_two_packages/migrate.py::WORD_ID_OFFSET 一致。
WORD_ID_OFFSET = 999_900_000

_THRESHOLD = 10**9  # 原始 id >= 10^9,已 shift id < 10^9


class InvalidMixedIdRangeError(ValueError):
    """同一 package 内同时出现原始(10^9+) 和已 shift(10^5) 两种 id — 异常数据。"""


def transform_body(
    raw_body: str, *, valid_new_ids: set[int]
) -> tuple[str | None, str, dict]:
    """Transform one OSS object body.

    Returns (new_body, status, details):
      - status="ok": new_body is the shifted JSON string
      - status="already_shifted": new_body is None, caller skips upload
      - status="dead_letter": new_body is None, details has missing_new_ids + source_old_ids

    Raises InvalidMixedIdRangeError when the body has a mix of 10^9+ and 10^5 ids.
    """
    parsed = json.loads(raw_body)
    all_ids = [w["id"] for u in parsed for w in u["words"]]
    if not all_ids:
        return raw_body, "ok", {}

    has_original = any(i >= _THRESHOLD for i in all_ids)
    has_shifted = any(i < _THRESHOLD for i in all_ids)
    if has_original and has_shifted:
        raise InvalidMixedIdRangeError(
            f"mixed id range: min={min(all_ids)} max={max(all_ids)}"
        )

    if not has_original:
        return None, "already_shifted", {}

    new_ids = [i - WORD_ID_OFFSET for i in all_ids]
    missing = sorted({n for n in new_ids if n not in valid_new_ids})
    if missing:
        return (
            None,
            "dead_letter",
            {
                "missing_new_ids": missing,
                "source_old_ids": [m + WORD_ID_OFFSET for m in missing],
            },
        )

    for u in parsed:
        for w in u["words"]:
            w["id"] -= WORD_ID_OFFSET

    new_body = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    return new_body, "ok", {}
```

- [ ] **Step 4: 运行测试确认 PASS**

Run: `.venv/bin/python -m pytest tests/packaging/test_shift_oss_package_word_ids.py -v`
Expected: `test_transform_ok_all_original_ids PASSED`

- [ ] **Step 5: Commit**

```bash
git add scripts/packaging/shift_oss_package_word_ids.py tests/packaging/test_shift_oss_package_word_ids.py
git commit -m "feat(oss-shift): transform_body core function + ok-branch test"
```

---

## Task 2: 覆盖 already_shifted / dead_letter / mixed-id 三个分支

补齐剩下的单测分支,确保 transform_body 的分支逻辑全部锁死。

**Files:**
- Modify: `tests/packaging/test_shift_oss_package_word_ids.py`

- [ ] **Step 1: 在测试文件末尾追加三个测试**

追加到 `tests/packaging/test_shift_oss_package_word_ids.py`:

```python
def test_transform_already_shifted_returns_none():
    valid = {100003, 100063}
    body = _body([[100003, 100063]])

    new_body, status, details = transform_body(body, valid_new_ids=valid)

    assert status == "already_shifted"
    assert new_body is None
    assert details == {}


def test_transform_dead_letter_when_id_missing_from_valid_set():
    valid = {100003}  # 只有 100003,缺 100063
    body = _body([[1_000_000_003, 1_000_000_063]])

    new_body, status, details = transform_body(body, valid_new_ids=valid)

    assert status == "dead_letter"
    assert new_body is None
    assert details["missing_new_ids"] == [100063]
    assert details["source_old_ids"] == [1_000_000_063]


def test_transform_raises_on_mixed_id_range():
    valid = {100003, 100063}
    body = _body([[1_000_000_003, 100063]])  # 一个原始 + 一个已 shift

    with pytest.raises(InvalidMixedIdRangeError):
        transform_body(body, valid_new_ids=valid)


def test_transform_empty_words_returns_ok():
    valid = set()
    body = json.dumps([{"id": 1, "title": "empty", "words": []}])

    new_body, status, details = transform_body(body, valid_new_ids=valid)

    assert status == "ok"
    assert new_body == body
    assert details == {}
```

- [ ] **Step 2: 运行测试确认 4 个新测试全 PASS**

Run: `.venv/bin/python -m pytest tests/packaging/test_shift_oss_package_word_ids.py -v`
Expected: 所有 5 个测试(1 老 + 4 新) PASSED

- [ ] **Step 3: Commit**

```bash
git add tests/packaging/test_shift_oss_package_word_ids.py
git commit -m "test(oss-shift): cover already_shifted / dead_letter / mixed-id branches"
```

---

## Task 3: PG 加载函数 + OSS client 构造

加 `load_valid_word_ids()` 和 `make_bucket()` 两个辅助,脚本骨架成形。

**Files:**
- Modify: `scripts/packaging/shift_oss_package_word_ids.py`

- [ ] **Step 1: 在脚本顶部添加 imports 和两个辅助函数**

在 `scripts/packaging/shift_oss_package_word_ids.py` 顶部 `from __future__` 之后、
`WORD_ID_OFFSET` 之前插入:

```python
import os
import sys
import time

import oss2
from sqlalchemy import create_engine, text
```

在 `InvalidMixedIdRangeError` 之后追加:

```python
def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_valid_word_ids(database_url: str) -> set[int]:
    """Load all word_id from domain.words into memory."""
    engine = create_engine(database_url, future=True)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT word_id FROM domain.words")).scalars().all()
    engine.dispose()
    return set(rows)


def make_bucket(endpoint: str, bucket_name: str, ak: str, sk: str) -> oss2.Bucket:
    """Construct an oss2.Bucket client."""
    auth = oss2.Auth(ak, sk)
    return oss2.Bucket(auth, endpoint, bucket_name)
```

- [ ] **Step 2: 运行已有测试确认没挂(加 import 不应影响纯函数)**

Run: `.venv/bin/python -m pytest tests/packaging/test_shift_oss_package_word_ids.py -v`
Expected: 5 测试全 PASSED

- [ ] **Step 3: 本地冒烟 — 确认 PG 连通**

```bash
source ~/.wordforge/prod.env
.venv/bin/python -c "
from scripts.packaging.shift_oss_package_word_ids import load_valid_word_ids
import os
ids = load_valid_word_ids(os.environ['DATABASE_URL'])
print(f'loaded {len(ids)} word ids; sample: {sorted(list(ids))[:5]} ... {sorted(list(ids))[-5:]}')
"
```
Expected: `loaded 121057 word ids` 左右,样本 id 都在 10 万级。

- [ ] **Step 4: 本地冒烟 — 确认 OSS list**

```bash
source ~/.wordforge/oss.env
.venv/bin/python -c "
from scripts.packaging.shift_oss_package_word_ids import make_bucket
import os, oss2
b = make_bucket(os.environ['OSS_ENDPOINT'], os.environ['OSS_BUCKET'], os.environ['OSS_ACCESS_KEY_ID'], os.environ['OSS_ACCESS_KEY_SECRET'])
n = sum(1 for _ in oss2.ObjectIterator(b))
print(f'bucket has {n} objects')
"
```
Expected: `bucket has 1443 objects`

- [ ] **Step 5: Commit**

```bash
git add scripts/packaging/shift_oss_package_word_ids.py
git commit -m "feat(oss-shift): add PG loader + OSS client helpers"
```

---

## Task 4: main() + CLI 参数 + dry-run/prod guard

串起整个主流程,不实际写回 OSS(put_object 在 Task 5 加)。这一步跑完能拉全量、
备份、transform、统计,但不写。

**Files:**
- Modify: `scripts/packaging/shift_oss_package_word_ids.py`

- [ ] **Step 1: 在脚本顶部 imports 处补充 argparse 和 Path**

在 `import oss2` 之前插入:

```python
import argparse
from pathlib import Path
```

(`json` / `os` / `sys` / `time` 已在前面 task 加过,不要重复。)

- [ ] **Step 2: 在文件末尾追加 main 函数**

追加到 `scripts/packaging/shift_oss_package_word_ids.py`:

```python
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Shift word_id by -999_900_000 across all package objects in "
        "OSS bucket sailing-words-package-words. Idempotent.",
    )
    p.add_argument("--bak-dir", type=Path, default=Path("./bak"))
    p.add_argument(
        "--dead-letter", type=Path, default=Path("./oss_shift_dead_letter.jsonl")
    )
    p.add_argument(
        "--i-am-writing-prod",
        action="store_true",
        help="Without this flag it is a dry run (backup + validate + transform only).",
    )
    return p.parse_args(argv)


def _require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        sys.exit(f"ERROR: missing env vars: {missing}. Did you source oss.env / prod.env?")
    return {n: os.environ[n] for n in names}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    args.bak_dir.mkdir(parents=True, exist_ok=True)

    env = _require_env(
        "DATABASE_URL",
        "OSS_ENDPOINT",
        "OSS_BUCKET",
        "OSS_ACCESS_KEY_ID",
        "OSS_ACCESS_KEY_SECRET",
    )

    _log(f"mode={'WRITE-PROD' if args.i_am_writing_prod else 'DRY-RUN'}")
    _log("stage 0: loading valid_new_ids from PG domain.words ...")
    valid_new_ids = load_valid_word_ids(env["DATABASE_URL"])
    _log(f"  loaded {len(valid_new_ids)} word ids")

    _log("stage 1: listing OSS bucket ...")
    bucket = make_bucket(
        env["OSS_ENDPOINT"],
        env["OSS_BUCKET"],
        env["OSS_ACCESS_KEY_ID"],
        env["OSS_ACCESS_KEY_SECRET"],
    )
    keys = [obj.key for obj in oss2.ObjectIterator(bucket)]
    _log(f"  found {len(keys)} objects")

    counts = {"ok": 0, "already_shifted": 0, "dead_letter": 0, "error": 0}
    with args.dead_letter.open("a", encoding="utf-8") as dl_fp:
        for i, key in enumerate(keys, 1):
            try:
                body = bucket.get_object(key).read().decode("utf-8")
            except oss2.exceptions.OssError as e:
                _log(f"  [{key}] download failed: {e}")
                dl_fp.write(json.dumps({"package_id": key, "reason": f"download: {e}"}) + "\n")
                counts["error"] += 1
                continue

            bak_path = args.bak_dir / f"{key}.json"
            if not bak_path.exists():
                bak_path.write_text(body, encoding="utf-8")

            try:
                new_body, status, details = transform_body(body, valid_new_ids=valid_new_ids)
            except InvalidMixedIdRangeError as e:
                _log(f"  [{key}] MIXED IDS: {e}")
                dl_fp.write(json.dumps({"package_id": key, "reason": f"mixed: {e}"}) + "\n")
                counts["error"] += 1
                continue

            if status == "dead_letter":
                dl_fp.write(json.dumps({"package_id": key, **details}) + "\n")
                counts["dead_letter"] += 1
            elif status == "already_shifted":
                counts["already_shifted"] += 1
            elif status == "ok":
                if args.i_am_writing_prod:
                    # put_object 放到 Task 5 再启用,这里先占位
                    pass
                counts["ok"] += 1

            if i % 100 == 0:
                _log(f"  progress: {i}/{len(keys)}  {counts}")

    _log(f"stage 3: summary = {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: 运行单测确认 transform 没退化**

Run: `.venv/bin/python -m pytest tests/packaging/test_shift_oss_package_word_ids.py -v`
Expected: 5 测试全 PASSED

- [ ] **Step 4: ruff 格式检查**

Run: `.venv/bin/ruff check scripts/packaging/shift_oss_package_word_ids.py tests/packaging/test_shift_oss_package_word_ids.py`
Expected: `All checks passed!`(若有 ruff 报错逐条修掉)

- [ ] **Step 5: dry-run 全量扫**

```bash
source ~/.wordforge/prod.env && source ~/.wordforge/oss.env
rm -rf ./bak ./oss_shift_dead_letter.jsonl
.venv/bin/python scripts/packaging/shift_oss_package_word_ids.py
```
Expected: `summary = {'ok': 1440, 'already_shifted': 3, 'dead_letter': 0, 'error': 0}`,
`./bak/` 下有 1443 个 json 文件,`./oss_shift_dead_letter.jsonl` 为空或不存在(我们
只在有条目时 append,因此 stage 结束后文件可能是空字节——以 `wc -l` 为准)。

如果 dead_letter > 0:**暂停实施**。把 dead-letter 文件内容贴给 allen,讨论是
`domain.words` 少词还是有别的数据漂移。这是 spec §4 明确要求"爆出来"的分支。

- [ ] **Step 6: Commit**

```bash
git add scripts/packaging/shift_oss_package_word_ids.py
git commit -m "feat(oss-shift): main loop with backup + dry-run default"
```

---

## Task 5: 启用 put_object,正式写 prod

在 Task 4 的 pass 占位处改成真正上传。

**Files:**
- Modify: `scripts/packaging/shift_oss_package_word_ids.py`

- [ ] **Step 1: 替换 Task 4 留下的 pass 占位**

找到 main() 里的这段:

```python
            elif status == "ok":
                if args.i_am_writing_prod:
                    # put_object 放到 Task 5 再启用,这里先占位
                    pass
                counts["ok"] += 1
```

改成:

```python
            elif status == "ok":
                if args.i_am_writing_prod:
                    try:
                        bucket.put_object(key, new_body.encode("utf-8"))
                    except oss2.exceptions.OssError as e:
                        _log(f"  [{key}] upload failed: {e}")
                        dl_fp.write(
                            json.dumps({"package_id": key, "reason": f"upload: {e}"}) + "\n"
                        )
                        counts["error"] += 1
                        continue
                counts["ok"] += 1
```

- [ ] **Step 2: 再跑单测**

Run: `.venv/bin/python -m pytest tests/packaging/test_shift_oss_package_word_ids.py -v`
Expected: 5 PASSED(transform_body 没动,应无影响)

- [ ] **Step 3: ruff 再过一遍**

Run: `.venv/bin/ruff check scripts/packaging/shift_oss_package_word_ids.py`
Expected: `All checks passed!`

- [ ] **Step 4: dry-run 再验证一次**

```bash
source ~/.wordforge/prod.env && source ~/.wordforge/oss.env
rm -rf ./bak ./oss_shift_dead_letter.jsonl
.venv/bin/python scripts/packaging/shift_oss_package_word_ids.py
```
Expected: 同 Task 4 Step 5 — `ok=1440 / already_shifted=3 / dead_letter=0 / error=0`。

- [ ] **Step 5: 正式写入 prod(需要 allen 口头确认)**

⚠️ 这一步需要 allen 明确说 "跑"。跑之前再确认一遍 `./bak/` 里已经有 1443 个备份。

```bash
ls ./bak/ | wc -l   # 期望 1443
source ~/.wordforge/prod.env && source ~/.wordforge/oss.env
.venv/bin/python scripts/packaging/shift_oss_package_word_ids.py --i-am-writing-prod
```
Expected: `summary = {'ok': 1440, 'already_shifted': 3, 'dead_letter': 0, 'error': 0}` +
所有 put_object 成功。

- [ ] **Step 6: post-check — 抽样 5 个 package 验证 max(id) < 10^7**

```bash
source ~/.wordforge/oss.env
.venv/bin/python - <<'PY'
import oss2, os, json, random
auth = oss2.Auth(os.environ["OSS_ACCESS_KEY_ID"], os.environ["OSS_ACCESS_KEY_SECRET"])
bucket = oss2.Bucket(auth, os.environ["OSS_ENDPOINT"], os.environ["OSS_BUCKET"])
keys = [o.key for o in oss2.ObjectIterator(bucket)]
print(f"total={len(keys)}")
random.seed(42)
for k in random.sample(keys, 5):
    body = bucket.get_object(k).read().decode()
    parsed = json.loads(body)
    ids = [w["id"] for u in parsed for w in u["words"]]
    print(f"{k}: min={min(ids)} max={max(ids)} n={len(ids)}")
    assert max(ids) < 10**7, f"{k} still has original ids"
print("all samples shifted OK")
PY
```
Expected: 5 个随机 package 的 max(id) 都 < 10^7,打印 "all samples shifted OK"。

- [ ] **Step 7: 幂等验证 — 再跑一次 dry-run**

```bash
.venv/bin/python scripts/packaging/shift_oss_package_word_ids.py
```
Expected: `summary = {'ok': 0, 'already_shifted': 1443, 'dead_letter': 0, 'error': 0}`。

- [ ] **Step 8: Commit**

```bash
git add scripts/packaging/shift_oss_package_word_ids.py
git commit -m "feat(oss-shift): enable put_object under --i-am-writing-prod"
```

---

## Self-Review

**Spec coverage 检查表:**

| Spec 章节 | Task 覆盖 |
|---|---|
| §1 做/不做 | Task 1-5 全部在边界内,未新建 schema / 未改 pipeline |
| §2 凭证 | Task 3 Step 3-4 冒烟,Task 4 `_require_env` |
| §3 数据现状 | Task 4 Step 5 dry-run 摸出来的 `1440/3/0` 作为对齐点 |
| §4 映射规则 + 校验 | Task 1-2 transform_body 所有分支 |
| §5 流程 stage 0-3 | Task 3(stage 0) + Task 4(stage 1-3) + Task 5(真写) |
| §6 CLI | Task 4 `_parse_args` + `--i-am-writing-prod` guard |
| §7 错误处理 | Task 4 `OssError` catch + Task 5 upload catch + `InvalidMixedIdRangeError` |
| §8 测试 | Task 1-2 pytest(ok/already/dead/mixed/empty 5 例) + Task 4/5 手工 |
| §9 目录 | `./bak/` 和 `./oss_shift_dead_letter.jsonl` 已在上一 commit 加入 .gitignore |
| §10 风险 | 误处理已 shift 的 3 个 → Task 5 Step 7 幂等 dry-run 兜底 |

**Placeholder 扫描:** 无 TBD/TODO/"fill in details";所有代码块完整。

**类型一致性:** `transform_body` 在 Task 1 定义返回 `(str | None, str, dict)`,Task 2 测试和 Task 4 main 调用处都按这个签名。`valid_new_ids: set[int]` 全程一致。

**遗留项:** Task 5 Step 5 需要 allen 口头确认才能执行,这是刻意的 prod-write gate。

---

## Execution Handoff

Plan 已写完。两种执行方式:

1. **Subagent-Driven(推荐)** — 一个 task 派一个 subagent,task 间由我 review,迭代快。
2. **Inline** — 我在当前 session 按 checkpoint 跑完,边跑边给你过。

你选哪种?
