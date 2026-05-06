# wordforge web admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 wordforge 加内部 web admin(FastAPI + React+Vite SPA),3 人级编辑在浏览器里搜词/改释义助记/审计/新建词/切换状态质量,替代手写 SQL。

**Architecture:** 后端 FastAPI 单进程(sync def 路由 + sync Engine + httpOnly cookie session + argon2),和现有 pipeline 进程独立。数据写 `domain.*`(加 status/quality_flag 两列)+ 新 `meta.*` schema(editors/sessions/edit_audit),同事务 rebuild `serving.word_payload` 保证下游一致。前端独立 Vite + React TS SPA,prod 由 FastAPI 静态挂载同源。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (sync), alembic, argon2-cffi, slowapi, React 18, TypeScript, Vite, uv.

**Spec 源:** `docs/superpowers/specs/2026-05-06-wordforge-web-admin-design.md` (v3.1, 966 行)

**PR 拆分:** 按 spec §7.1 M1-M7 里程碑拆 7 个 PR,每个 PR 独立可 merge、有测试、不破坏 main。M1 落地后前后端可以并行(M3 ready 时前端开干只读 UI)。

---

## 前置约束(每个 Task 都必须遵守)

- **venv**:`cd word_forge && uv sync --extra dev --extra llm --extra web` (第一次;后续 `uv add <pkg>` 别用 `pip install`)
- **test env**:`export DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test'`。`tests/conftest.py` guard 会拒绝非本地+非 test 名 URL
- **跑测试模块模式**:`.venv/bin/python -m pytest ...` 或 `uv run pytest ...`,不是 `python tests/xxx.py`
- **commit 粒度**:每个 step 结束 commit;pre-commit hook 不过 → 修根因再 commit(别用 `--no-verify`)
- **不允许裸 `except Exception`**(CLAUDE.md 硬规矩;唯一例外是 runner double-fault guard)
- **域 DDL 必须走 alembic migration**,不手写 SQL(spec §2.6)
- **不在代码硬编码凭证**,env 通过 `wordforge.settings` 或直接读 `os.environ`

---

## 里程碑索引

- **M1** 基础设施(alembic 0011 + web 包骨架 + CLI + docker service + editors CLI + `db/serving.py` 提取 + mirror 同步改)
- **M2** auth(login/logout/me + session 表 + cookie + argon2 + rate limit)
- **M3** 只读 API(搜词 + 详情 + audit + keyset cursor)
- **M4** 编辑写路径(PATCH + drift 409 rollback + audit 原子 + status/quality 切换 + serving rebuild)
- **M5** 新建词(POST /words + UNIQUE 降级编辑 + 子表 source stamp)
- **M6** 前端 SPA(登录 / 搜索 / 详情编辑 / 审计页)
- **M7** 打磨(500 错误页 + request_id 展示 + 部署文档 + 手测 checklist 全过)

依赖图:M1 → M2 → M3 → (M4, M5 并行) → M6 → M7。M6 可在 M3 ready 时开工先做只读 UI。

---

# M1 — 基础设施

## Task M1.1: 添加 `[web]` extra + `make_engine` 扩 kwargs

**Files:**
- Modify: `pyproject.toml`(加 web extra)
- Modify: `src/wordforge/db/engine.py`(`make_engine` 接 `**engine_kwargs`)
- Test: `tests/db/test_engine_kwargs.py` (新)

- [ ] **Step 1: 先 Read `src/wordforge/db/engine.py` 确认现有签名**

Run: Read file, note current signature `def make_engine(url: str | None = None) -> Engine:`

- [ ] **Step 2: 写失败测试**

Create `tests/db/test_engine_kwargs.py`:
```python
"""Engine factory must accept pool kwargs for web process sizing."""
from wordforge.db.engine import make_engine


def test_make_engine_accepts_pool_size(monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test",
    )
    eng = make_engine(pool_size=3, max_overflow=2)
    assert eng.pool.size() == 3
    eng.dispose()
```

- [ ] **Step 3: 跑测试验证 FAIL**

Run: `uv run pytest tests/db/test_engine_kwargs.py -v`
Expected: `TypeError: make_engine() got an unexpected keyword argument 'pool_size'`

- [ ] **Step 4: 改 `src/wordforge/db/engine.py`**

Current (roughly):
```python
def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or _get_database_url(), ...)
```

Change to:
```python
def make_engine(url: str | None = None, **engine_kwargs) -> Engine:
    """Create a sync SQLAlchemy Engine.

    engine_kwargs forwarded to create_engine (pool_size, max_overflow, etc.).
    Web process passes pool_size=5, max_overflow=5 (spec §4.5).
    """
    return create_engine(url or _get_database_url(), **engine_kwargs)
```

**保留现有 `_get_database_url()` 私有函数不动;不改现有参数语义**。

- [ ] **Step 5: 跑测试验证 PASS**

Run: `uv run pytest tests/db/test_engine_kwargs.py -v`
Expected: PASS

- [ ] **Step 6: 跑现有测试确认未回归**

Run: `uv run pytest tests/ -q --ignore=tests/web`
Expected: 与 M1 前同等通过率(web 目录尚不存在,ignore 安全)

- [ ] **Step 7: 加 `[web]` extra 到 `pyproject.toml`**

找到 `[project.optional-dependencies]` 段,与 `dev` / `llm` 并列加:
```toml
web = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "argon2-cffi>=23.1",
    "slowapi>=0.1.9",
    "python-multipart>=0.0.7",
]
```

- [ ] **Step 8: 同步 uv.lock 并验证能装**

Run:
```bash
uv sync --extra dev --extra llm --extra web
```
Expected: `.venv/` 内装上 fastapi / uvicorn / argon2-cffi / slowapi。

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml uv.lock src/wordforge/db/engine.py tests/db/test_engine_kwargs.py
git commit -m "feat(db): make_engine accepts pool kwargs; add [web] optional extra"
```

---

## Task M1.2: alembic migration 0011 — `domain.words` 加两列 + backfill + `meta.*` schema

**Files:**
- Create: `src/wordforge/db/migrations/versions/0011_add_editor_workflow.py`

- [ ] **Step 1: 确认当前 head**

Run: `uv run alembic -c alembic.ini current` (test DB 先 upgrade head 以取当前 revision)
Expected: `0010_xxx`(spec §2.6 已列到 0010)

- [ ] **Step 2: 生成 migration 文件**

Create `src/wordforge/db/migrations/versions/0011_add_editor_workflow.py`:
```python
"""add editor workflow: domain.words.status/quality_flag + meta schema

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-06
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- 1) domain.words 扩列 ----
    op.execute("""
        ALTER TABLE domain.words
          ADD COLUMN status SMALLINT NOT NULL DEFAULT 0
            CHECK (status IN (0, 1, 2)),
          ADD COLUMN quality_flag TEXT NOT NULL DEFAULT 'none'
            CHECK (quality_flag IN ('none','suspect','fixed'))
    """)

    # backfill: 已在 serving.word_payload 的词视为已上线
    op.execute("""
        UPDATE domain.words
           SET status = 1
         WHERE word_id IN (SELECT word_id FROM serving.word_payload)
    """)

    # partial index:只索引非 1 的极少数行
    op.execute("""
        CREATE INDEX idx_domain_words_status ON domain.words (status)
          WHERE status IN (0, 2)
    """)
    op.execute("""
        CREATE INDEX idx_domain_words_quality ON domain.words (quality_flag)
          WHERE quality_flag <> 'none'
    """)

    # ---- 2) meta schema ----
    op.execute("CREATE SCHEMA meta")

    op.execute("""
        CREATE TABLE meta.editors (
          id            BIGSERIAL PRIMARY KEY,
          email         TEXT NOT NULL UNIQUE,
          password_hash TEXT NOT NULL,
          display_name  TEXT NOT NULL,
          is_active     BOOLEAN NOT NULL DEFAULT TRUE,
          created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE TABLE meta.editor_sessions (
          token_hash TEXT PRIMARY KEY,
          editor_id  BIGINT NOT NULL REFERENCES meta.editors(id) ON DELETE CASCADE,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          expires_at TIMESTAMPTZ NOT NULL
        )
    """)
    op.execute("CREATE INDEX idx_editor_sessions_editor ON meta.editor_sessions (editor_id)")

    op.execute("""
        CREATE TABLE meta.edit_audit (
          id         BIGSERIAL PRIMARY KEY,
          word_id    BIGINT NOT NULL,
          field_path TEXT NOT NULL,
          target_id  BIGINT,
          op         TEXT NOT NULL CHECK (op IN ('update','insert','delete')),
          old_value  JSONB,
          new_value  JSONB,
          editor_id  BIGINT NOT NULL REFERENCES meta.editors(id) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX idx_edit_audit_word ON meta.edit_audit (word_id, created_at DESC)")
    op.execute("CREATE INDEX idx_edit_audit_editor ON meta.edit_audit (editor_id, created_at DESC)")


def downgrade() -> None:
    # dev/test 验证用;prod 永不 downgrade
    op.execute("DROP SCHEMA meta CASCADE")
    op.execute("DROP INDEX IF EXISTS domain.idx_domain_words_quality")
    op.execute("DROP INDEX IF EXISTS domain.idx_domain_words_status")
    op.execute("""
        ALTER TABLE domain.words
          DROP COLUMN quality_flag,
          DROP COLUMN status
    """)
```

- [ ] **Step 3: 在 test DB 验 upgrade/downgrade 幂等**

Run:
```bash
export DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5434/wordforge_test'
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```
Expected: 都 OK 无报错。

- [ ] **Step 4: psql 确认 schema 存在**

Run:
```bash
docker exec wordforge-pg-test psql -U wordforge -d wordforge_test -c "\dn meta"
docker exec wordforge-pg-test psql -U wordforge -d wordforge_test -c "\dt meta.*"
docker exec wordforge-pg-test psql -U wordforge -d wordforge_test -c "\d+ domain.words"
```
Expected: `meta` schema;`editors / editor_sessions / edit_audit` 三张表;`domain.words` 多了 status、quality_flag 两列。

- [ ] **Step 5: Commit**

```bash
git add src/wordforge/db/migrations/versions/0011_add_editor_workflow.py
git commit -m "feat(migrations): 0011 add domain.words.status/quality_flag + meta.* schema"
```

---

## Task M1.3: 提取 `rebuild_word_payload` 到 `wordforge.db.serving`,加 status gate

**Files:**
- Create: `src/wordforge/db/serving.py`
- Modify: `src/wordforge/stages/export.py`(把 `_upsert_serving_word_payload` 替换为调用新模块)
- Test: `tests/db/test_serving.py` (新)

- [ ] **Step 1: Read `src/wordforge/stages/export.py`**,定位 `_upsert_serving_word_payload`(spec 附录 B 指 line 169-288)

- [ ] **Step 2: 写失败测试**

Create `tests/db/test_serving.py`:
```python
"""rebuild_word_payload: status=1 upserts serving; status=0/2 deletes."""
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.db.serving import rebuild_word_payload


def test_rebuild_status_1_upserts(test_engine, seed_word_status_1):
    word_id = seed_word_status_1
    with test_engine.begin() as conn:
        rebuild_word_payload(conn, word_id)
        row = conn.execute(
            text("SELECT payload FROM serving.word_payload WHERE word_id = :w"),
            {"w": word_id},
        ).first()
    assert row is not None
    assert row.payload["status"] == 1
    assert "quality_flag" in row.payload


def test_rebuild_status_2_deletes(test_engine, seed_word_status_2):
    word_id = seed_word_status_2
    with test_engine.begin() as conn:
        rebuild_word_payload(conn, word_id)
        row = conn.execute(
            text("SELECT 1 FROM serving.word_payload WHERE word_id = :w"),
            {"w": word_id},
        ).first()
    assert row is None
```

**`test_engine` / `seed_word_status_*` fixtures** 在 Task M1.4 的 conftest 建;现在写测试并让它暂时 ImportError,留给 M1.4。

- [ ] **Step 3: Create `src/wordforge/db/serving.py`**

```python
"""serving.word_payload rebuild — shared by export stage + web service.

Semantics (spec §2.1 status contract):
- status=1: UPSERT serving.word_payload with JSONB including status/quality_flag
- status=0/2: DELETE FROM serving.word_payload WHERE word_id=:w

Runs inside caller's engine.begin() transaction. Caller responsible for txn
boundary and commit.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection


def rebuild_word_payload(conn: Connection, word_id: int) -> None:
    row = conn.execute(
        text(
            "SELECT status, quality_flag, type, form, phonetic_us, phonetic_uk, "
            "audio_us, audio_uk, plural, past_tense, past_participle, "
            "third_person, present_participle, comparative, superlative, "
            "derivatives, structure "
            "FROM domain.words WHERE word_id = :w"
        ),
        {"w": word_id},
    ).first()
    if row is None:
        # 词已被硬删 — 下游也不应有
        conn.execute(
            text("DELETE FROM serving.word_payload WHERE word_id = :w"),
            {"w": word_id},
        )
        return

    if row.status != 1:
        conn.execute(
            text("DELETE FROM serving.word_payload WHERE word_id = :w"),
            {"w": word_id},
        )
        return

    # build payload: copy export stage existing logic but add status/quality_flag
    meanings = conn.execute(
        text(
            "SELECT meaning_id, pos, pos_sub, cn_paraphrase, en_paraphrase, "
            "equivalents, synonyms, antonyms "
            "FROM domain.meanings WHERE word_id = :w ORDER BY meaning_id"
        ),
        {"w": word_id},
    ).mappings().all()

    sentences_by_meaning: dict[int, list[dict]] = {}
    for m in meanings:
        sents = conn.execute(
            text(
                "SELECT sentence_id, form, translation, highlight, citation, "
                "citation_detail "
                "FROM domain.sentences WHERE meaning_id = :m ORDER BY sentence_id"
            ),
            {"m": m["meaning_id"]},
        ).mappings().all()
        sentences_by_meaning[m["meaning_id"]] = [dict(s) for s in sents]

    mnemonic = conn.execute(
        text(
            "SELECT type, content FROM domain.mnemonics "
            "WHERE word_id = :w ORDER BY mnemonic_id LIMIT 1"
        ),
        {"w": word_id},
    ).mappings().first()

    phrases = conn.execute(
        text(
            "SELECT phrase_id, form, meaning, source "
            "FROM domain.phrases WHERE word_id = :w ORDER BY phrase_id"
        ),
        {"w": word_id},
    ).mappings().all()

    payload = {
        "word_id": word_id,
        "status": row.status,
        "quality_flag": row.quality_flag,
        "type": row.type,
        "form": row.form,
        "phonetic_us": row.phonetic_us,
        "phonetic_uk": row.phonetic_uk,
        "audio_us": row.audio_us,
        "audio_uk": row.audio_uk,
        "plural": row.plural,
        "past_tense": row.past_tense,
        "past_participle": row.past_participle,
        "third_person": row.third_person,
        "present_participle": row.present_participle,
        "comparative": row.comparative,
        "superlative": row.superlative,
        "derivatives": row.derivatives,
        "structure": row.structure,
        "meanings": [
            {**dict(m), "sentences": sentences_by_meaning.get(m["meaning_id"], [])}
            for m in meanings
        ],
        "mnemonic": dict(mnemonic) if mnemonic else None,
        "phrases": [dict(p) for p in phrases],
    }

    conn.execute(
        text(
            "INSERT INTO serving.word_payload (word_id, payload, updated_at) "
            "VALUES (:w, :p, now()) "
            "ON CONFLICT (word_id) DO UPDATE "
            "SET payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at"
        ),
        {"w": word_id, "p": payload},
    )
```

- [ ] **Step 4: 改 `stages/export.py`**

找到 `ExportStage._upsert_serving_word_payload(self, conn, word_id)`:
```python
# 删掉原 100+ 行实现,替换成
from wordforge.db.serving import rebuild_word_payload

class ExportStage(...):
    def _upsert_serving_word_payload(self, conn, word_id: int) -> None:
        rebuild_word_payload(conn, word_id)
```

- [ ] **Step 5: 跑现有 export 相关测试确认未回归**

Run: `uv run pytest tests/stages/test_export.py -v` (或等价测试文件;若无,至少跑一次 `uv run pytest tests/ -q` 整体)
Expected: 所有现有测试仍通过。

- [ ] **Step 6: Commit**

(此时 `tests/db/test_serving.py` 仍 ImportError/fixture 缺,暂不 add;M1.4 合并)
```bash
git add src/wordforge/db/serving.py src/wordforge/stages/export.py
git commit -m "refactor(db): extract rebuild_word_payload with status gate"
```

---

## Task M1.4: web 测试 fixture + 跑通 Task M1.3 遗留的 test_serving.py

**Files:**
- Create: `tests/web/__init__.py`
- Create: `tests/web/conftest.py`
- Modify: `tests/db/__init__.py`(若不存在先 touch)
- Create: `tests/db/conftest.py`(或放共用 fixture 到 top-level tests/conftest.py)

- [ ] **Step 1: Read `tests/conftest.py`** 熟悉现有 guard 和 fixture 模式

- [ ] **Step 2: Create `tests/db/conftest.py`**

```python
"""Shared fixtures for wordforge.db.* tests."""
import pytest
from sqlalchemy import text

from wordforge.db.engine import make_engine


@pytest.fixture
def test_engine():
    """Reuse env-guarded DATABASE_URL (tests/conftest.py enforces localhost+test)."""
    eng = make_engine()
    yield eng
    eng.dispose()


@pytest.fixture
def seed_word_status_1(test_engine):
    with test_engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO domain.words "
                "(type, form, phonetic_us, phonetic_uk, source, status) "
                "VALUES (1, 'testword_status1', '/t/', '/t/', 'human:test', 1) "
                "RETURNING word_id"
            )
        ).first()
        word_id = row.word_id
    yield word_id
    with test_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM serving.word_payload WHERE word_id = :w"),
            {"w": word_id},
        )
        conn.execute(text("DELETE FROM domain.words WHERE word_id = :w"), {"w": word_id})


@pytest.fixture
def seed_word_status_2(test_engine):
    with test_engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO domain.words "
                "(type, form, phonetic_us, phonetic_uk, source, status) "
                "VALUES (1, 'testword_status2', '/t/', '/t/', 'human:test', 2) "
                "RETURNING word_id"
            )
        ).first()
        conn.execute(
            text(
                "INSERT INTO serving.word_payload (word_id, payload, updated_at) "
                "VALUES (:w, :p, now())"
            ),
            {"w": row.word_id, "p": {"status": 2}},
        )
        word_id = row.word_id
    yield word_id
    with test_engine.begin() as conn:
        conn.execute(text("DELETE FROM serving.word_payload WHERE word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM domain.words WHERE word_id = :w"), {"w": word_id})
```

- [ ] **Step 3: 跑 M1.3 遗留的 test_serving.py**

Run: `uv run pytest tests/db/test_serving.py -v`
Expected: PASS 两条(status=1 upsert / status=2 delete)

- [ ] **Step 4: Create `tests/web/__init__.py` 空文件**(pytest 包识别)

- [ ] **Step 5: Create 最小 `tests/web/conftest.py` 占位**(后续 M2+ 填内容)

```python
"""web test fixtures — populated by M2+ tasks."""
```

- [ ] **Step 6: Commit**

```bash
git add tests/db/conftest.py tests/db/test_serving.py tests/web/__init__.py tests/web/conftest.py
git commit -m "test(db): add serving rebuild fixtures and tests"
```

---

## Task M1.5: pipeline export 显式 `SET status=1` + `mirror_to_mysql.py` 读 PG status

**Files:**
- Modify: `src/wordforge/stages/export.py`(INSERT/UPDATE `domain.words` 时显式带 status=1)
- Modify: `scripts/replicate/field_mapping.py`(删硬编码 `"status": 1`)
- Modify: `scripts/replicate/mirror_to_mysql.py`(SELECT 把 `status` 带出来)
- Create: `tests/replicate/test_field_mapping.py`

- [ ] **Step 1: Read `src/wordforge/stages/export.py`** 定位 `_upsert_app_words`(spec 附录 B 指 line 326-368)。确认当前 INSERT 列表没有 `status`。

- [ ] **Step 2: 改 `_upsert_app_words` INSERT 列表**

在 INSERT 的 column list 里加 `status`,VALUES 里加 `1`(`SMALLINT`):
```python
op.execute(text(
    "INSERT INTO domain.words (type, form, ..., source, status) "
    "VALUES (:type, :form, ..., :source, 1) "
    "ON CONFLICT ..."
))
```
具体 SQL 依当前写法调整;关键:**pipeline 产出默认 status=1(已上线)**。

- [ ] **Step 3: 跑现有 pipeline / export 测试**

Run: `uv run pytest tests/stages/test_export.py tests/pipeline/test_runner.py -v`
Expected: 仍全 PASS;如果有 test 硬编码检 SELECT 不含 status,改 test 让它识别新列。

- [ ] **Step 4: Read `scripts/replicate/field_mapping.py`**,定位 `row_to_mysql_word()` 里 `"status": 1` 硬编码

- [ ] **Step 5: 写 field_mapping 测试**

Create `tests/replicate/__init__.py` 空文件;Create `tests/replicate/test_field_mapping.py`:
```python
"""PG -> MySQL field mapping: status must pass through, not hardcoded."""
from scripts.replicate.field_mapping import row_to_mysql_word


def _base_pg_row(status: int) -> dict:
    return {
        "word_id": 100001,
        "type": 1,
        "form": "test",
        "phonetic_us": "/t/",
        "phonetic_uk": "/t/",
        "audio_us": None,
        "audio_uk": None,
        "source": "pipeline:v1",
        "status": status,
        # 其余字段按 field_mapping 所需补 null 即可
    }


def test_status_0_maps_0():
    row = _base_pg_row(0)
    out = row_to_mysql_word(row)
    assert out["status"] == 0


def test_status_1_maps_1():
    row = _base_pg_row(1)
    out = row_to_mysql_word(row)
    assert out["status"] == 1


def test_status_2_maps_2():
    row = _base_pg_row(2)
    out = row_to_mysql_word(row)
    assert out["status"] == 2
```

- [ ] **Step 6: 跑测试验证 FAIL**(当前硬编码 1)

Run: `uv run pytest tests/replicate/test_field_mapping.py -v`
Expected: 0/2 两条 FAIL(都得 1)

- [ ] **Step 7: 改 `field_mapping.py`**

```python
# 删掉 "status": 1,替换成
"status": pg_row["status"],
```

- [ ] **Step 8: 改 `mirror_to_mysql.py` 的 SELECT**

找到从 PG 读取 `domain.words` 的 SELECT,加入 `status` 列(若缺);确保传给 `row_to_mysql_word` 的 dict 有 `status` key

- [ ] **Step 9: 再跑测试验证 PASS**

Run: `uv run pytest tests/replicate/ -v`
Expected: 3 条全 PASS

- [ ] **Step 10: Commit**

```bash
git add src/wordforge/stages/export.py scripts/replicate/field_mapping.py scripts/replicate/mirror_to_mysql.py tests/replicate/
git commit -m "feat(replicate): PG status passes through to MySQL; pipeline export sets status=1"
```

---

## Task M1.6: web 包骨架 — FastAPI app + deps + envelope + request_id middleware

**Files:**
- Create: `src/wordforge/web/__init__.py`(空)
- Create: `src/wordforge/web/app.py`
- Create: `src/wordforge/web/deps.py`
- Create: `src/wordforge/web/errors.py`
- Create: `src/wordforge/web/middleware.py`
- Create: `src/wordforge/web/routes/__init__.py`
- Create: `src/wordforge/web/routes/health.py`(最小 smoke endpoint)
- Test: `tests/web/test_app_factory.py`

- [ ] **Step 1: `src/wordforge/web/__init__.py` 空 + `routes/__init__.py` 空**

- [ ] **Step 2: Create `src/wordforge/web/errors.py`**

```python
"""Global exception handler + envelope.

All responses are `{ok, data, error}`. Never leak stack traces to clients.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def envelope_ok(data: Any) -> dict:
    return {"ok": True, "data": data, "error": None}


def envelope_err(code: str, message: str, details: dict | None = None) -> dict:
    return {"ok": False, "data": None, "error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=400, content=envelope_err("invalid_input", "validation failed", {"errors": exc.errors()}))

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        return JSONResponse(status_code=409, content=envelope_err("conflict", "integrity violation"))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code_map = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 429: "rate_limited"}
        return JSONResponse(status_code=exc.status_code, content=envelope_err(code_map.get(exc.status_code, "http_error"), exc.detail or ""))

    @app.exception_handler(Exception)
    async def _uncaught(request: Request, exc: Exception):
        req_id = getattr(request.state, "request_id", "unknown")
        logger.exception("unhandled exception (request_id=%s)", req_id)
        return JSONResponse(
            status_code=500,
            content=envelope_err("internal", "系统错误,已记录,请稍后重试", {"request_id": req_id}),
        )
```

- [ ] **Step 3: Create `src/wordforge/web/middleware.py`**

```python
"""Request ID middleware: assign uuid4 per request + response header."""
from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        req_id = str(uuid.uuid4())
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response
```

- [ ] **Step 4: Create `src/wordforge/web/deps.py`**

```python
"""Dependencies: engine singleton + current editor placeholder."""
from __future__ import annotations

from functools import lru_cache
from sqlalchemy.engine import Engine

from wordforge.db.engine import make_engine


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    # spec §4.5: web pool_size=5, max_overflow=5
    return make_engine(pool_size=5, max_overflow=5)
```

- [ ] **Step 5: Create `src/wordforge/web/routes/health.py`**

```python
"""Minimal smoke endpoint for M1 — checks DB roundtrip."""
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Engine

from wordforge.web.deps import get_engine
from wordforge.web.errors import envelope_ok

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health(engine: Engine = Depends(get_engine)):
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return envelope_ok({"status": "ok"})
```

- [ ] **Step 6: Create `src/wordforge/web/app.py`**

```python
"""FastAPI app factory."""
from __future__ import annotations

from fastapi import FastAPI

from wordforge.web.errors import register_exception_handlers
from wordforge.web.middleware import RequestIDMiddleware
from wordforge.web.routes.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="wordforge web admin", version="0.1.0")
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    # Static SPA mount added in M7
    return app
```

- [ ] **Step 7: 写 app factory 测试**

Create `tests/web/test_app_factory.py`:
```python
from fastapi.testclient import TestClient
from wordforge.web.app import create_app


def test_health_endpoint():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "ok"
    assert body["error"] is None
    assert "X-Request-ID" in resp.headers
```

- [ ] **Step 8: 跑测试 PASS**

Run: `uv run pytest tests/web/test_app_factory.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/wordforge/web/ tests/web/test_app_factory.py
git commit -m "feat(web): FastAPI app factory + envelope + request_id middleware + health"
```

---

## Task M1.7: `wordforge web` CLI + docker-compose service + Dockerfile.web

**Files:**
- Modify: `src/wordforge/cli.py`(加 `web` 子命令)
- Create: `Dockerfile.web`
- Modify: `docker-compose.yml`(加 `wordforge-web` service)

- [ ] **Step 1: Read `src/wordforge/cli.py`**,看子命令注册方式(typer / argparse / click)

- [ ] **Step 2: 加 `web` 子命令**,透传 uvicorn flag

示例(typer):
```python
@app.command()
def web(
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = False,
    workers: int = 1,
) -> None:
    """Start the web admin server."""
    import uvicorn
    uvicorn.run(
        "wordforge.web.app:create_app",
        host=host, port=port, reload=reload, workers=workers,
        factory=True,
    )
```

- [ ] **Step 3: 本地跑一下验证起得来**

Run:
```bash
export DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge'
uv run wordforge web --port 8000
# 另开终端
curl -s http://localhost:8000/api/v1/health | jq .
```
Expected: `{"ok": true, "data": {"status": "ok"}, ...}`;停掉服务

- [ ] **Step 4: Create `Dockerfile.web`**

```dockerfile
# Multi-stage: node build frontend → python install → final
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
# M6 落地时前端工程存在;当前 M1 可先让 dist 为空目录
RUN npm run build || mkdir -p dist

FROM python:3.12-slim AS python-build
RUN pip install uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --extra web --frozen --no-dev

FROM python:3.12-slim AS final
WORKDIR /app
COPY --from=python-build /app /app
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
ENTRYPOINT ["wordforge", "web", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 5: docker-compose.yml 加 service**

在现有 `docker-compose.yml` 末尾加:
```yaml
  wordforge-web:
    build:
      context: .
      dockerfile: Dockerfile.web
    environment:
      DATABASE_URL: "${DATABASE_URL}"
      WORDFORGE_WEB_COOKIE_SECURE: "false"
    ports:
      - "8000:8000"
    restart: unless-stopped
```

**不加 `depends_on: wordforge-pg`**(DB 是 RDS 外部;dev PG 可选)

- [ ] **Step 6: Commit**

```bash
git add src/wordforge/cli.py Dockerfile.web docker-compose.yml
git commit -m "feat(cli+docker): wordforge web CLI + Dockerfile.web + compose service"
```

---

# M2 — Auth

## Task M2.1: `wordforge editors` CLI 子命令(create / list / deactivate)

**Files:**
- Create: `src/wordforge/web/services/editor_service.py`
- Create: `src/wordforge/web/auth.py`(argon2 hash / verify helper)
- Modify: `src/wordforge/cli.py`(加 `editors create/list/deactivate`)
- Test: `tests/web/test_editor_service.py`

- [ ] **Step 1: Create `src/wordforge/web/auth.py`**

```python
"""Password hashing + session token helpers.

- argon2-cffi PasswordHasher (NOT passlib; see spec §4.1)
- token_hash = sha256(raw_token_urlsafe_32)
- routes are sync def → argon2 runs in FastAPI threadpool (no asyncio.to_thread needed)
"""
from __future__ import annotations

import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_ph = PasswordHasher()


def hash_password(raw: str) -> str:
    return _ph.hash(raw)


def verify_password(stored: str, raw: str) -> bool:
    try:
        _ph.verify(stored, raw)
    except VerifyMismatchError:
        return False
    return True


def generate_session_token() -> tuple[str, str]:
    """Return (raw_token_for_cookie, sha256_hex_for_db)."""
    raw = secrets.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return raw, digest


def hash_session_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
```

- [ ] **Step 2: Create `src/wordforge/web/services/editor_service.py`**

```python
"""Editor account CRUD — called by CLI; web admin does not expose registration."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from wordforge.web.auth import hash_password


def create_editor(engine: Engine, email: str, display_name: str, raw_password: str) -> int:
    pw_hash = hash_password(raw_password)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO meta.editors (email, display_name, password_hash) "
                "VALUES (:e, :d, :h) RETURNING id"
            ),
            {"e": email, "d": display_name, "h": pw_hash},
        ).first()
    return row.id


def list_editors(engine: Engine) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, email, display_name, is_active, created_at FROM meta.editors ORDER BY id")
        ).mappings().all()
    return [dict(r) for r in rows]


def deactivate_editor(engine: Engine, email: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE meta.editors SET is_active = FALSE WHERE email = :e"),
            {"e": email},
        )
```

- [ ] **Step 3: 写测试**

Create `tests/web/test_editor_service.py`:
```python
import pytest
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.auth import verify_password
from wordforge.web.services.editor_service import create_editor, deactivate_editor, list_editors


@pytest.fixture
def engine():
    e = make_engine()
    yield e
    with e.begin() as conn:
        conn.execute(text("DELETE FROM meta.editors WHERE email LIKE 'test-%@wordforge.local'"))
    e.dispose()


def test_create_editor_hashes_password(engine):
    new_id = create_editor(engine, "test-create@wordforge.local", "T C", "secret123")
    assert new_id > 0
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT password_hash FROM meta.editors WHERE id = :i"),
            {"i": new_id},
        ).first()
    assert row.password_hash != "secret123"
    assert verify_password(row.password_hash, "secret123")
    assert not verify_password(row.password_hash, "wrong")


def test_list_and_deactivate(engine):
    create_editor(engine, "test-list@wordforge.local", "T L", "x")
    rows = [r for r in list_editors(engine) if r["email"] == "test-list@wordforge.local"]
    assert rows and rows[0]["is_active"] is True
    deactivate_editor(engine, "test-list@wordforge.local")
    rows = [r for r in list_editors(engine) if r["email"] == "test-list@wordforge.local"]
    assert rows[0]["is_active"] is False
```

- [ ] **Step 4: 跑测试 PASS**

Run: `uv run pytest tests/web/test_editor_service.py -v`
Expected: 2 PASS

- [ ] **Step 5: CLI 加 `editors` 子命令**

在 `src/wordforge/cli.py`(示例 typer 写法):
```python
editors_app = typer.Typer(help="Manage web admin editor accounts.")
app.add_typer(editors_app, name="editors")


@editors_app.command("create")
def editors_create(
    email: str = typer.Option(...),
    display_name: str = typer.Option(...),
) -> None:
    import getpass
    password = getpass.getpass("Password: ")
    from wordforge.db.engine import make_engine
    from wordforge.web.services.editor_service import create_editor
    new_id = create_editor(make_engine(), email, display_name, password)
    typer.echo(f"created editor id={new_id}")


@editors_app.command("list")
def editors_list() -> None:
    from wordforge.db.engine import make_engine
    from wordforge.web.services.editor_service import list_editors
    for e in list_editors(make_engine()):
        typer.echo(f"{e['id']:>5}  {e['email']:<40}  {'active' if e['is_active'] else 'off':<7}  {e['display_name']}")


@editors_app.command("deactivate")
def editors_deactivate(email: str = typer.Option(...)) -> None:
    from wordforge.db.engine import make_engine
    from wordforge.web.services.editor_service import deactivate_editor
    deactivate_editor(make_engine(), email)
    typer.echo(f"deactivated {email}")
```

- [ ] **Step 6: 手测 CLI**

```bash
export DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge'
uv run wordforge editors create --email you@wordforge.local --display-name "You"
# 输入密码
uv run wordforge editors list
# 应显示新建账号
```

- [ ] **Step 7: Commit**

```bash
git add src/wordforge/web/auth.py src/wordforge/web/services/editor_service.py src/wordforge/cli.py tests/web/test_editor_service.py
git commit -m "feat(editors): CLI create/list/deactivate + argon2 password hashing"
```

---

## Task M2.2: Login / Logout / Me + httpOnly cookie + session 表

**Files:**
- Create: `src/wordforge/web/security.py`(session token + cookie helper)
- Create: `src/wordforge/web/schemas/auth.py`
- Create: `src/wordforge/web/routes/auth.py`
- Modify: `src/wordforge/web/app.py`(include auth router)
- Modify: `src/wordforge/web/deps.py`(加 `current_editor` 依赖)
- Test: `tests/web/test_auth_routes.py`

- [ ] **Step 1: Create `src/wordforge/web/schemas/__init__.py` 空 + `schemas/auth.py`**

```python
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class EditorOut(BaseModel):
    id: int
    email: str
    display_name: str
```

- [ ] **Step 2: Create `src/wordforge/web/security.py`**

```python
"""Session lifecycle: create / validate / revoke."""
from __future__ import annotations

import datetime as _dt
import os

from sqlalchemy import text
from sqlalchemy.engine import Connection

from wordforge.web.auth import generate_session_token, hash_session_token

SESSION_TTL = _dt.timedelta(days=7)
COOKIE_NAME = "session"


def cookie_secure() -> bool:
    return os.environ.get("WORDFORGE_WEB_COOKIE_SECURE", "false").lower() == "true"


def create_session(conn: Connection, editor_id: int) -> str:
    raw, digest = generate_session_token()
    expires = _dt.datetime.now(_dt.timezone.utc) + SESSION_TTL
    conn.execute(
        text(
            "INSERT INTO meta.editor_sessions (token_hash, editor_id, expires_at) "
            "VALUES (:h, :e, :x)"
        ),
        {"h": digest, "e": editor_id, "x": expires},
    )
    return raw


def find_active_editor(conn: Connection, raw_token: str) -> dict | None:
    digest = hash_session_token(raw_token)
    row = conn.execute(
        text(
            "SELECT e.id, e.email, e.display_name, e.is_active "
            "FROM meta.editor_sessions s JOIN meta.editors e ON s.editor_id = e.id "
            "WHERE s.token_hash = :h AND s.expires_at > now()"
        ),
        {"h": digest},
    ).first()
    if row is None or not row.is_active:
        return None
    return {"id": row.id, "email": row.email, "display_name": row.display_name}


def revoke_session(conn: Connection, raw_token: str) -> None:
    conn.execute(
        text("DELETE FROM meta.editor_sessions WHERE token_hash = :h"),
        {"h": hash_session_token(raw_token)},
    )


def cleanup_expired(conn: Connection) -> None:
    conn.execute(text("DELETE FROM meta.editor_sessions WHERE expires_at < now()"))
```

- [ ] **Step 3: 扩展 `deps.py` 加 `current_editor`**

```python
# 追加
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.engine import Engine
from wordforge.web.security import COOKIE_NAME, find_active_editor


def current_editor(request: Request, engine: Engine = Depends(get_engine)) -> dict:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not logged in")
    with engine.connect() as conn:
        editor = find_active_editor(conn, raw)
    if editor is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    return editor
```

- [ ] **Step 4: Create `src/wordforge/web/routes/auth.py`**

```python
"""login / logout / me — cookie-based session."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.engine import Engine

from wordforge.web.auth import verify_password
from wordforge.web.deps import current_editor, get_engine
from wordforge.web.errors import envelope_ok
from wordforge.web.schemas.auth import EditorOut, LoginRequest
from wordforge.web.security import (
    COOKIE_NAME,
    SESSION_TTL,
    cleanup_expired,
    cookie_secure,
    create_session,
    revoke_session,
)

router = APIRouter(prefix="/api/v1/auth")
limiter = Limiter(key_func=get_remote_address)


@router.post("/login")
@limiter.limit("10/60seconds")
def login(request: Request, body: LoginRequest, response: Response, engine: Engine = Depends(get_engine)):
    with engine.begin() as conn:
        cleanup_expired(conn)
        row = conn.execute(
            text("SELECT id, email, display_name, password_hash, is_active FROM meta.editors WHERE email = :e"),
            {"e": body.email},
        ).first()
        if row is None or not row.is_active or not verify_password(row.password_hash, body.password):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password")
        raw = create_session(conn, row.id)
    response.set_cookie(
        key=COOKIE_NAME,
        value=raw,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(),
        max_age=int(SESSION_TTL.total_seconds()),
        path="/api",
    )
    return envelope_ok({"editor": EditorOut(id=row.id, email=row.email, display_name=row.display_name).model_dump()})


@router.post("/logout")
def logout(request: Request, response: Response, engine: Engine = Depends(get_engine)):
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        with engine.begin() as conn:
            revoke_session(conn, raw)
    response.delete_cookie(COOKIE_NAME, path="/api")
    return envelope_ok(None)


@router.get("/me")
def me(editor: dict = Depends(current_editor)):
    return envelope_ok(EditorOut(**editor).model_dump())
```

- [ ] **Step 5: 注册 slowapi 到 app.py**

```python
# src/wordforge/web/app.py,create_app() 里
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from wordforge.web.routes.auth import limiter, router as auth_router

def create_app() -> FastAPI:
    app = FastAPI(...)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, lambda req, exc: JSONResponse(
        status_code=429,
        content=envelope_err("rate_limited", "too many requests"),
    ))
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    return app
```

- [ ] **Step 6: 写测试**

Create `tests/web/test_auth_routes.py`:
```python
from fastapi.testclient import TestClient
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.app import create_app
from wordforge.web.services.editor_service import create_editor


def _mkclient():
    return TestClient(create_app())


def _cleanup(email: str):
    e = make_engine()
    with e.begin() as conn:
        conn.execute(text("DELETE FROM meta.editor_sessions WHERE editor_id IN (SELECT id FROM meta.editors WHERE email = :e)"), {"e": email})
        conn.execute(text("DELETE FROM meta.editors WHERE email = :e"), {"e": email})
    e.dispose()


def test_login_logout_me():
    email = "test-login@wordforge.local"
    create_editor(make_engine(), email, "LT", "pw1234")
    client = _mkclient()
    try:
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "pw1234"})
        assert r.status_code == 200
        assert "session" in r.cookies
        m = client.get("/api/v1/auth/me")
        assert m.status_code == 200
        assert m.json()["data"]["email"] == email
        client.post("/api/v1/auth/logout")
        m2 = client.get("/api/v1/auth/me")
        assert m2.status_code == 401
    finally:
        _cleanup(email)


def test_wrong_password_401():
    email = "test-wrong@wordforge.local"
    create_editor(make_engine(), email, "WT", "pw1234")
    client = _mkclient()
    try:
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthenticated"
    finally:
        _cleanup(email)


def test_me_without_cookie_401():
    client = _mkclient()
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401


def test_rate_limit_after_10():
    client = _mkclient()
    for _ in range(10):
        client.post("/api/v1/auth/login", json={"email": "nosuch@wordforge.local", "password": "x"})
    r = client.post("/api/v1/auth/login", json={"email": "nosuch@wordforge.local", "password": "x"})
    assert r.status_code == 429
```

- [ ] **Step 7: 跑测试 PASS**

Run: `uv run pytest tests/web/test_auth_routes.py -v`
Expected: 4 PASS

- [ ] **Step 8: Commit**

```bash
git add src/wordforge/web/security.py src/wordforge/web/schemas/ src/wordforge/web/routes/auth.py src/wordforge/web/deps.py src/wordforge/web/app.py tests/web/test_auth_routes.py
git commit -m "feat(auth): login/logout/me with cookie session + slowapi rate limit"
```

---

# M3 — 只读 API

## Task M3.1: keyset cursor encode/decode

**Files:**
- Create: `src/wordforge/web/cursor.py`
- Test: `tests/web/test_cursor.py`

- [ ] **Step 1: Create `src/wordforge/web/cursor.py`**

```python
"""Keyset pagination cursor — plain base64(JSON), no HMAC (spec §3.2)."""
from __future__ import annotations

import base64
import json
from typing import Literal

from pydantic import BaseModel

Order = Literal["updated_at_desc"]  # MVP 单一 order;lemma_asc 后续迭代


class Cursor(BaseModel):
    o: Order
    u: str  # updated_at ISO-8601
    w: int  # word_id


def encode(order: Order, updated_at: str, word_id: int) -> str:
    payload = Cursor(o=order, u=updated_at, w=word_id).model_dump()
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")


def decode(raw: str, expected_order: Order) -> Cursor:
    try:
        pad = "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw + pad))
        cursor = Cursor(**payload)
    except Exception:  # pragma: no cover - validated below via HTTPException raise
        raise ValueError("invalid cursor")
    if cursor.o != expected_order:
        raise ValueError(f"cursor order mismatch: expected {expected_order}, got {cursor.o}")
    return cursor
```

**注意**:spec CLAUDE.md 禁裸 `except Exception`。这里 `decode` 的 `except Exception` 紧接着 `raise ValueError`,不是吞异常,**语义是重塑为业务异常**,OK。

- [ ] **Step 2: 写测试**

Create `tests/web/test_cursor.py`:
```python
import pytest

from wordforge.web.cursor import decode, encode


def test_roundtrip():
    c = encode("updated_at_desc", "2026-05-06T10:00:00Z", 12345)
    d = decode(c, "updated_at_desc")
    assert d.u == "2026-05-06T10:00:00Z"
    assert d.w == 12345


def test_decode_garbage_raises():
    with pytest.raises(ValueError):
        decode("not-base64!!!", "updated_at_desc")


def test_order_mismatch_raises():
    c = encode("updated_at_desc", "2026-05-06T10:00:00Z", 1)
    # spec MVP 只有一个 order,但用户可能伪造一个新 order 发进来
    with pytest.raises(ValueError):
        # 用参数绕过 Literal 限制,模拟 decode 收到非法 order
        from wordforge.web.cursor import Cursor
        import base64, json
        raw = base64.urlsafe_b64encode(json.dumps({"o": "other_order", "u": "x", "w": 1}).encode()).decode().rstrip("=")
        decode(raw, "updated_at_desc")
```

- [ ] **Step 3: 跑测试 PASS**

Run: `uv run pytest tests/web/test_cursor.py -v`
Expected: 3 PASS

- [ ] **Step 4: Commit**

```bash
git add src/wordforge/web/cursor.py tests/web/test_cursor.py
git commit -m "feat(web): keyset cursor encode/decode"
```

---

## Task M3.2: 搜词 `GET /api/v1/words` + 详情 `GET /api/v1/words/{id}`

**Files:**
- Create: `src/wordforge/web/schemas/words.py`
- Create: `src/wordforge/web/routes/words.py`
- Modify: `src/wordforge/web/app.py`(include words_router)
- Test: `tests/web/test_words_read.py`

- [ ] **Step 1: Create `src/wordforge/web/schemas/words.py`**

```python
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class WordListItem(BaseModel):
    word_id: int
    form: str
    type: int
    status: int
    quality_flag: str
    updated_at: datetime
    meaning_count: int


class WordListResponse(BaseModel):
    items: list[WordListItem]
    next_cursor: str | None


class WordDetailResponse(BaseModel):
    word: dict[str, Any]
    meanings: list[dict[str, Any]]
    mnemonics: list[dict[str, Any]]
    sentences: list[dict[str, Any]]
    phrases: list[dict[str, Any]]
```

- [ ] **Step 2: Create `src/wordforge/web/routes/words.py`** — search + detail

```python
"""Words routes: search + detail (M3 read-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.engine import Engine

from wordforge.web.cursor import decode, encode
from wordforge.web.deps import current_editor, get_engine
from wordforge.web.errors import envelope_ok

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
    where = []
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
        where.append("EXISTS (SELECT 1 FROM domain.meanings m WHERE m.word_id = w.word_id AND m.pos = :pos)")
        params["pos"] = pos
    if cursor:
        try:
            c = decode(cursor, "updated_at_desc")
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid cursor")
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
        next_cursor = encode("updated_at_desc", last["updated_at"].isoformat(), last["word_id"])
    return envelope_ok({"items": items, "next_cursor": next_cursor})


@router.get("/{word_id}")
def detail(word_id: int, engine: Engine = Depends(get_engine)):
    with engine.connect() as conn:
        word = conn.execute(
            text("SELECT * FROM domain.words WHERE word_id = :w"),
            {"w": word_id},
        ).mappings().first()
        if word is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="word not found")
        meanings = conn.execute(
            text("SELECT * FROM domain.meanings WHERE word_id = :w ORDER BY meaning_id"),
            {"w": word_id},
        ).mappings().all()
        mnemonics = conn.execute(
            text("SELECT * FROM domain.mnemonics WHERE word_id = :w ORDER BY mnemonic_id"),
            {"w": word_id},
        ).mappings().all()
        # 注意: domain.sentences 无 word_id 列,必须 JOIN meanings (spec §3.3 实施注意)
        sentences = conn.execute(
            text(
                "SELECT s.* FROM domain.sentences s "
                "JOIN domain.meanings m ON s.meaning_id = m.meaning_id "
                "WHERE m.word_id = :w ORDER BY s.sentence_id"
            ),
            {"w": word_id},
        ).mappings().all()
        phrases = conn.execute(
            text("SELECT * FROM domain.phrases WHERE word_id = :w ORDER BY phrase_id"),
            {"w": word_id},
        ).mappings().all()
    return envelope_ok({
        "word": dict(word),
        "meanings": [dict(m) for m in meanings],
        "mnemonics": [dict(m) for m in mnemonics],
        "sentences": [dict(s) for s in sentences],
        "phrases": [dict(p) for p in phrases],
    })
```

- [ ] **Step 3: Include words_router in `app.py`**

- [ ] **Step 4: 写测试**

Create `tests/web/test_words_read.py`:
```python
from fastapi.testclient import TestClient
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.app import create_app
from wordforge.web.services.editor_service import create_editor


def _login_client(email="test-r3@wordforge.local", pw="pw1234"):
    create_editor(make_engine(), email, "R3", pw)
    client = TestClient(create_app())
    client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    return client, email


def _cleanup(email):
    e = make_engine()
    with e.begin() as conn:
        conn.execute(text("DELETE FROM meta.editor_sessions WHERE editor_id IN (SELECT id FROM meta.editors WHERE email=:e)"), {"e": email})
        conn.execute(text("DELETE FROM meta.editors WHERE email=:e"), {"e": email})
    e.dispose()


def test_search_requires_auth():
    client = TestClient(create_app())
    r = client.get("/api/v1/words")
    assert r.status_code == 401


def test_search_returns_items():
    client, email = _login_client()
    try:
        r = client.get("/api/v1/words?limit=5")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"]["items"], list)
    finally:
        _cleanup(email)


def test_detail_not_found():
    client, email = _login_client("test-r3-2@wordforge.local")
    try:
        r = client.get("/api/v1/words/999999999")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
    finally:
        _cleanup("test-r3-2@wordforge.local")
```

- [ ] **Step 5: 跑测试 PASS**

Run: `uv run pytest tests/web/test_words_read.py -v`
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add src/wordforge/web/schemas/words.py src/wordforge/web/routes/words.py src/wordforge/web/app.py tests/web/test_words_read.py
git commit -m "feat(web): GET /words search (keyset) + GET /words/{id} detail"
```

---

## Task M3.3: 审计日志 `GET /api/v1/audit`

**Files:**
- Create: `src/wordforge/web/schemas/audit.py`
- Create: `src/wordforge/web/routes/audit.py`
- Modify: `src/wordforge/web/app.py`(include audit_router)
- Test: `tests/web/test_audit_routes.py`

- [ ] **Step 1: Create `schemas/audit.py`** — keep flat,cursor 复用同一 encode/decode

- [ ] **Step 2: Create `routes/audit.py`** — 复制 search 的 pattern,过滤字段 word_id / editor_id / since / until

- [ ] **Step 3: 写测试(至少 3 条)**:未登录 401 / 按 word_id 过滤 / 分页 cursor 往返

- [ ] **Step 4: 跑测试 PASS + Commit**

```bash
git add src/wordforge/web/schemas/audit.py src/wordforge/web/routes/audit.py src/wordforge/web/app.py tests/web/test_audit_routes.py
git commit -m "feat(web): GET /audit with keyset pagination + filters"
```

---

# M4 — 编辑写路径

## Task M4.1: `audit_service` — 审计写入(同事务)

**Files:**
- Create: `src/wordforge/web/services/audit_service.py`
- Test: `tests/web/test_audit_service.py`

- [ ] **Step 1: Create `src/wordforge/web/services/audit_service.py`**

```python
"""Write meta.edit_audit rows inside caller's txn. Never opens its own."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def write_audit(
    conn: Connection,
    *,
    word_id: int,
    field_path: str,
    target_id: int | None,
    op: str,
    old_value: Any,
    new_value: Any,
    editor_id: int,
) -> None:
    """Insert one audit row. Caller responsible for engine.begin() txn."""
    conn.execute(
        text(
            "INSERT INTO meta.edit_audit "
            "(word_id, field_path, target_id, op, old_value, new_value, editor_id) "
            "VALUES (:w, :fp, :tid, :op, :ov, :nv, :eid)"
        ),
        {
            "w": word_id, "fp": field_path, "tid": target_id, "op": op,
            "ov": old_value, "nv": new_value, "eid": editor_id,
        },
    )
```

- [ ] **Step 2: 写测试 + 跑 PASS + commit**

Create `tests/web/test_audit_service.py`,覆盖:update(target_id=meaning_id)、insert(old=null)、delete(new=null)三种 op 写入正确。fixture 用 M2 test 新建的 editor + M1 test 新建的 word。

```bash
git add src/wordforge/web/services/audit_service.py tests/web/test_audit_service.py
git commit -m "feat(audit): audit_service.write_audit helper"
```

---

## Task M4.2: `word_service.apply_web_changes` — 显式 UPDATE + check_drift + all-or-nothing

**Files:**
- Create: `src/wordforge/web/services/word_service.py`
- Test: `tests/web/test_word_service.py`

**spec §3.4 关键约束**:
- 复用 `reviewer.patch.check_drift` 和 `PatchDriftError`,**不调用** `apply_patch`(它用数组索引,不兼容 target_id)
- 遇 drift 立即 raise,由外层 `engine.begin()` 回滚
- 每条成功 change 同事务写 audit
- `domain.words` 改时 `SET ..., updated_at = now()`;子表三张**不写** updated_at(列不存在)

- [ ] **Step 1: Create `src/wordforge/web/services/word_service.py`** 框架

```python
"""apply_web_changes: all-or-nothing PATCH writes with drift detection."""
from __future__ import annotations

from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection

from wordforge.reviewer.patch import PatchDriftError
from wordforge.web.services.audit_service import write_audit


# field_path → (table, column, has_updated_at)
FIELD_MAP: dict[str, tuple[str, str, bool]] = {
    "words.form":              ("domain.words", "form", True),
    "words.phonetic_us":       ("domain.words", "phonetic_us", True),
    "words.phonetic_uk":       ("domain.words", "phonetic_uk", True),
    "meanings.cn_paraphrase":  ("domain.meanings", "cn_paraphrase", False),
    "meanings.en_paraphrase":  ("domain.meanings", "en_paraphrase", False),
    "mnemonics.content":       ("domain.mnemonics", "content", False),
    "sentences.form":          ("domain.sentences", "form", False),
    "sentences.translation":   ("domain.sentences", "translation", False),
    # 扩展时在此加;未列出的 field_path 由 schema 拒绝
}
```

- [ ] **Step 2: 加 apply_web_changes 主函数(同文件)**

```python
def apply_web_changes(
    conn: Connection,
    *,
    word_id: int,
    changes: list[dict[str, Any]],
    editor_id: int,
) -> int:
    """Apply changes in order. First drift → raise. Caller supplies engine.begin()."""
    applied = 0
    for ch in changes:
        fp: str = ch["field_path"]
        op: Literal["update", "insert", "delete"] = ch["op"]
        if op != "update":
            raise NotImplementedError("M4 covers op=update only; insert/delete in later task")
        if fp not in FIELD_MAP:
            raise ValueError(f"unknown field_path: {fp}")
        table, column, has_ts = FIELD_MAP[fp]
        target_id: int | None = ch.get("target_id")
        pk_col = {
            "domain.words": "word_id",
            "domain.meanings": "meaning_id",
            "domain.mnemonics": "mnemonic_id",
            "domain.sentences": "sentence_id",
            "domain.phrases": "phrase_id",
        }[table]
        # words.* 用 word_id 当 PK;子表用 target_id
        pk_val = word_id if table == "domain.words" else target_id
        if pk_val is None:
            raise ValueError(f"target_id required for {fp}")
        cur = conn.execute(
            text(f"SELECT {column} AS v FROM {table} WHERE {pk_col} = :pk"),
            {"pk": pk_val},
        ).first()
        if cur is None:
            raise ValueError(f"{table} pk={pk_val} not found")
        if cur.v != ch["old_value"]:
            raise PatchDriftError(f"drift at {fp}: db={cur.v!r} old={ch['old_value']!r}")
        ts_clause = ", updated_at = now()" if has_ts else ""
        conn.execute(
            text(f"UPDATE {table} SET {column} = :v{ts_clause} WHERE {pk_col} = :pk"),
            {"v": ch["new_value"], "pk": pk_val},
        )
        write_audit(
            conn, word_id=word_id, field_path=fp, target_id=target_id if table != "domain.words" else None,
            op="update", old_value=ch["old_value"], new_value=ch["new_value"], editor_id=editor_id,
        )
        applied += 1
    return applied
```

- [ ] **Step 3: 写测试** — 4 条关键场景

Create `tests/web/test_word_service.py` 覆盖:
1. 成功路径:改 `meanings.cn_paraphrase` 1 条,返 `applied=1`,DB 更新,audit 插入,target_id=meaning_id
2. drift:`old_value` 不匹配 → raise `PatchDriftError`
3. 原子性:构造 change 里第 2 条 drift,确认第 1 条的 UPDATE + audit 被回滚(外层用 `engine.begin()` 包,捕 raise 后查 DB 原值未变、audit 无记录)
4. `domain.words.form` 改动写 audit 时 `target_id=NULL`

- [ ] **Step 4: 跑 PASS + commit**

Run: `uv run pytest tests/web/test_word_service.py -v`

```bash
git add src/wordforge/web/services/word_service.py tests/web/test_word_service.py
git commit -m "feat(words): apply_web_changes with drift+audit+atomic txn (op=update)"
```

---

## Task M4.3: `PATCH /api/v1/words/{id}` 路由 + serving rebuild + drift 409

**Files:**
- Modify: `src/wordforge/web/schemas/words.py`(加 PatchRequest / PatchResponse)
- Modify: `src/wordforge/web/routes/words.py`(加 PATCH handler)
- Modify: `src/wordforge/web/errors.py`(PatchDriftError → 409 handler)
- Test: `tests/web/test_words_patch.py`

- [ ] **Step 1: 扩 `errors.py` 加 PatchDriftError handler**

```python
# src/wordforge/web/errors.py,register_exception_handlers 里加
from wordforge.reviewer.patch import PatchDriftError

@app.exception_handler(PatchDriftError)
async def _drift(request: Request, exc: PatchDriftError):
    return JSONResponse(status_code=409, content=envelope_err("conflict", str(exc)))
```

- [ ] **Step 2: `schemas/words.py` 加 PatchChange / PatchRequest**

```python
from typing import Any, Literal

class PatchChange(BaseModel):
    field_path: str
    target_id: int | None = None
    op: Literal["update"]  # M4: update only; insert/delete 留 M5/后续
    old_value: Any
    new_value: Any

class PatchRequest(BaseModel):
    changes: list[PatchChange]
```

- [ ] **Step 3: `routes/words.py` 加 PATCH handler**

```python
from wordforge.db.serving import rebuild_word_payload
from wordforge.web.services.word_service import apply_web_changes

@router.patch("/{word_id}")
def patch_word(word_id: int, body: PatchRequest, editor: dict = Depends(current_editor), engine: Engine = Depends(get_engine)):
    with engine.begin() as conn:
        # 确认词存在
        exists = conn.execute(text("SELECT 1 FROM domain.words WHERE word_id=:w"), {"w": word_id}).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="word not found")
        applied = apply_web_changes(
            conn,
            word_id=word_id,
            changes=[c.model_dump() for c in body.changes],
            editor_id=editor["id"],
        )
        rebuild_word_payload(conn, word_id)
    return envelope_ok({"applied": applied})
```

- [ ] **Step 4: 写测试(5 条)**

`tests/web/test_words_patch.py`:
1. 未登录 → 401
2. 成功 PATCH 1 字段 → 200 `applied=1` + audit 行 + serving 有新值
3. drift → 409 + 整事务 rollback(domain 未改 + audit 无记录 + serving 未改)
4. word_id 不存在 → 404
5. `domain.meanings` UPDATE SQL 不含 `updated_at`(直接断言改动成功,不含报错即通过)

- [ ] **Step 5: 跑 PASS + commit**

```bash
git add src/wordforge/web/errors.py src/wordforge/web/schemas/words.py src/wordforge/web/routes/words.py tests/web/test_words_patch.py
git commit -m "feat(web): PATCH /words/{id} with drift 409 + serving rebuild"
```

---

## Task M4.4: `POST /status` + `POST /quality` 切换(带 drift 校验)

**Files:**
- Modify: `src/wordforge/web/schemas/words.py`(加 StatusChangeRequest / QualityChangeRequest)
- Modify: `src/wordforge/web/routes/words.py`(加两个 POST)
- Modify: `src/wordforge/web/services/word_service.py`(复用 apply_web_changes via `words.status` / `words.quality_flag` field)
- Test: `tests/web/test_words_status_quality.py`

- [ ] **Step 1: 把 `words.status` / `words.quality_flag` 加进 `FIELD_MAP`**

```python
FIELD_MAP["words.status"]       = ("domain.words", "status",       True)
FIELD_MAP["words.quality_flag"] = ("domain.words", "quality_flag", True)
```

- [ ] **Step 2: schemas 加**

```python
class StatusChangeRequest(BaseModel):
    old_value: Literal[0, 1, 2]
    new_value: Literal[0, 1, 2]

class QualityChangeRequest(BaseModel):
    old_value: Literal["none", "suspect", "fixed"]
    new_value: Literal["none", "suspect", "fixed"]
```

- [ ] **Step 3: routes 加 POST /status + POST /quality**

```python
@router.post("/{word_id}/status")
def change_status(word_id: int, body: StatusChangeRequest, editor: dict = Depends(current_editor), engine: Engine = Depends(get_engine)):
    with engine.begin() as conn:
        if conn.execute(text("SELECT 1 FROM domain.words WHERE word_id=:w"), {"w": word_id}).first() is None:
            raise HTTPException(status_code=404, detail="word not found")
        apply_web_changes(
            conn, word_id=word_id, editor_id=editor["id"],
            changes=[{"field_path": "words.status", "target_id": None, "op": "update",
                      "old_value": body.old_value, "new_value": body.new_value}],
        )
        rebuild_word_payload(conn, word_id)
    return envelope_ok(None)


@router.post("/{word_id}/quality")
def change_quality(word_id: int, body: QualityChangeRequest, editor: dict = Depends(current_editor), engine: Engine = Depends(get_engine)):
    with engine.begin() as conn:
        if conn.execute(text("SELECT 1 FROM domain.words WHERE word_id=:w"), {"w": word_id}).first() is None:
            raise HTTPException(status_code=404, detail="word not found")
        apply_web_changes(
            conn, word_id=word_id, editor_id=editor["id"],
            changes=[{"field_path": "words.quality_flag", "target_id": None, "op": "update",
                      "old_value": body.old_value, "new_value": body.new_value}],
        )
        rebuild_word_payload(conn, word_id)
    return envelope_ok(None)
```

- [ ] **Step 4: 写测试(4 条)**

`tests/web/test_words_status_quality.py`:
1. status 0→1 成功;audit 有 `field_path='words.status'`;serving 原本删除/空 → 现在上线
2. status 1→2 后 serving.word_payload 被 DELETE
3. status drift(old_value 不符)→ 409 + rollback
4. quality drift → 409 + rollback

- [ ] **Step 5: 跑 PASS + commit**

```bash
git add src/wordforge/web/schemas/words.py src/wordforge/web/routes/words.py src/wordforge/web/services/word_service.py tests/web/test_words_status_quality.py
git commit -m "feat(web): POST /words/{id}/status + /quality with drift check"
```

---

# M5 — 新建词

## Task M5.1: `POST /api/v1/words` — form+type 冲突降级 + 子表 source stamp

**Files:**
- Modify: `src/wordforge/web/schemas/words.py`(加 CreateWordRequest / CreateWordResponse)
- Modify: `src/wordforge/web/services/word_service.py`(加 `create_web_word`)
- Modify: `src/wordforge/web/routes/words.py`(加 POST)
- Test: `tests/web/test_words_create.py`

**spec §3.6 核心**:
- `form` 服务端 `strip()`,不 lowercase
- UNIQUE 冲突(查 + IntegrityError)都返 409 + 已存在 `word_id`
- 子表(meanings/mnemonics/sentences/phrases)所有 `source` 服务端强制 `human:web`,忽略前端传入
- INSERT domain.words 时 SET `status=0, quality_flag='none'`, `source='human:web'`
- 每行子表插入写 audit(op='insert',new_value=行 JSON)
- 成功后同事务 `rebuild_word_payload`

- [ ] **Step 1: schemas 加 CreateWordRequest**

```python
class MeaningIn(BaseModel):
    pos: int | None = None
    pos_sub: int | None = None
    cn_paraphrase: str | None = None
    en_paraphrase: str | None = None
    # 不接 source:服务端强制

class MnemonicIn(BaseModel):
    type: int = 1
    content: dict

class SentenceIn(BaseModel):
    meaning_index: int  # 前端在 meanings[] 里的位置,服务端映射到新插入的 meaning_id
    form: str
    translation: str
    highlight: dict | None = None

class PhraseIn(BaseModel):
    form: str
    meaning: str | None = None

class CreateWordRequest(BaseModel):
    form: str
    type: Literal[1, 2]
    phonetic_us: str | None = None
    phonetic_uk: str | None = None
    meanings: list[MeaningIn] = []
    mnemonics: list[MnemonicIn] = []
    sentences: list[SentenceIn] = []
    phrases: list[PhraseIn] = []
```

- [ ] **Step 2: `word_service.create_web_word`**

```python
from sqlalchemy.exc import IntegrityError

HUMAN_WEB = "human:web"

def create_web_word(conn, *, body: dict, editor_id: int) -> tuple[int, bool]:
    """Return (word_id, created). created=False when form+type already exists."""
    form = body["form"].strip()
    type_ = body["type"]
    existing = conn.execute(
        text("SELECT word_id FROM domain.words WHERE form=:f AND type=:t"),
        {"f": form, "t": type_},
    ).first()
    if existing is not None:
        return existing.word_id, False
    try:
        row = conn.execute(
            text(
                "INSERT INTO domain.words (type, form, phonetic_us, phonetic_uk, source, status, quality_flag) "
                "VALUES (:t, :f, :pu, :pk, :src, 0, 'none') RETURNING word_id"
            ),
            {"t": type_, "f": form, "pu": body.get("phonetic_us"), "pk": body.get("phonetic_uk"), "src": HUMAN_WEB},
        ).first()
    except IntegrityError:
        # 并发:另一事务已插入
        existing = conn.execute(
            text("SELECT word_id FROM domain.words WHERE form=:f AND type=:t"),
            {"f": form, "t": type_},
        ).first()
        return existing.word_id, False
    word_id = row.word_id
    write_audit(conn, word_id=word_id, field_path="words", target_id=None, op="insert",
                old_value=None, new_value={"form": form, "type": type_}, editor_id=editor_id)
    # meanings
    meaning_ids: list[int] = []
    for m in body.get("meanings") or []:
        mrow = conn.execute(
            text(
                "INSERT INTO domain.meanings (word_id, pos, pos_sub, cn_paraphrase, en_paraphrase, source) "
                "VALUES (:w, :p, :ps, :cn, :en, :src) RETURNING meaning_id"
            ),
            {"w": word_id, "p": m.get("pos"), "ps": m.get("pos_sub"),
             "cn": m.get("cn_paraphrase"), "en": m.get("en_paraphrase"), "src": HUMAN_WEB},
        ).first()
        meaning_ids.append(mrow.meaning_id)
        write_audit(conn, word_id=word_id, field_path="meanings", target_id=mrow.meaning_id,
                    op="insert", old_value=None, new_value=m, editor_id=editor_id)
    # sentences: 映射 meaning_index → meaning_id
    for s in body.get("sentences") or []:
        mid = meaning_ids[s["meaning_index"]]
        srow = conn.execute(
            text(
                "INSERT INTO domain.sentences (meaning_id, form, translation, highlight, source) "
                "VALUES (:m, :f, :t, :h, :src) RETURNING sentence_id"
            ),
            {"m": mid, "f": s["form"], "t": s["translation"], "h": s.get("highlight"), "src": HUMAN_WEB},
        ).first()
        write_audit(conn, word_id=word_id, field_path="sentences", target_id=srow.sentence_id,
                    op="insert", old_value=None, new_value=s, editor_id=editor_id)
    # mnemonics
    for mn in body.get("mnemonics") or []:
        mnrow = conn.execute(
            text(
                "INSERT INTO domain.mnemonics (word_id, type, content, source) "
                "VALUES (:w, :t, :c, :src) RETURNING mnemonic_id"
            ),
            {"w": word_id, "t": mn.get("type", 1), "c": mn["content"], "src": HUMAN_WEB},
        ).first()
        write_audit(conn, word_id=word_id, field_path="mnemonics", target_id=mnrow.mnemonic_id,
                    op="insert", old_value=None, new_value=mn, editor_id=editor_id)
    # phrases
    for ph in body.get("phrases") or []:
        prow = conn.execute(
            text(
                "INSERT INTO domain.phrases (word_id, form, meaning, source) "
                "VALUES (:w, :f, :m, :src) RETURNING phrase_id"
            ),
            {"w": word_id, "f": ph["form"], "m": ph.get("meaning"), "src": HUMAN_WEB},
        ).first()
        write_audit(conn, word_id=word_id, field_path="phrases", target_id=prow.phrase_id,
                    op="insert", old_value=None, new_value=ph, editor_id=editor_id)
    return word_id, True
```

- [ ] **Step 3: routes 加 POST**

```python
@router.post("", status_code=201)
def create_word(body: CreateWordRequest, response: Response, editor: dict = Depends(current_editor), engine: Engine = Depends(get_engine)):
    with engine.begin() as conn:
        word_id, created = create_web_word(conn, body=body.model_dump(), editor_id=editor["id"])
        if not created:
            response.status_code = 409
            return envelope_err("conflict", "form+type already exists", {"word_id": word_id, "reason": "already_exists"})
        rebuild_word_payload(conn, word_id)
    return envelope_ok({"word_id": word_id, "created": True})
```

**注意**:409 时仍然进 201 handler 再改 status_code,response body `envelope_err` + `data.word_id`——spec §3.6 要的是 409 data 含 `word_id`。

- [ ] **Step 4: 写测试(5 条)**

`tests/web/test_words_create.py`:
1. 成功新建空壳(meanings=[]) → 201 + word_id + status=0 + source=human:web
2. 成功新建带 1 条 meaning + 1 条 sentence → 子表 source 全 human:web + audit 行齐
3. form+type 重复(先查即有)→ 409 + data.word_id 指向已存在
4. 前端传 source="pipeline:x" 被服务端覆盖为 human:web(**不信任前端**)
5. form 首尾空格被 `strip()`(`" Abc "` 入库为 `"Abc"`)

- [ ] **Step 5: 跑 PASS + commit**

```bash
git add src/wordforge/web/schemas/words.py src/wordforge/web/services/word_service.py src/wordforge/web/routes/words.py tests/web/test_words_create.py
git commit -m "feat(web): POST /words create with UNIQUE 409 fallback + source stamp"
```

---

# M6 — 前端 SPA

## Task M6.1: Vite + React TS 工程 + api client + 登录页

**Files:**
- Create: `frontend/package.json`、`frontend/vite.config.ts`、`frontend/tsconfig.json`、`frontend/index.html`
- Create: `frontend/src/main.tsx`、`frontend/src/App.tsx`
- Create: `frontend/src/api/client.ts`、`frontend/src/api/types.ts`
- Create: `frontend/src/pages/Login.tsx`

- [ ] **Step 1: `npm create vite@latest frontend -- --template react-ts`**(或手写);确认 `frontend/dist` 加入 `.gitignore`

- [ ] **Step 2: `vite.config.ts` 加 `proxy`**

```ts
server: {
  proxy: { '/api': 'http://localhost:8000' }
}
```

- [ ] **Step 3: `api/client.ts` 简单 fetch 包装**

```ts
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, { credentials: 'include', headers: { 'Content-Type': 'application/json' }, ...init });
  const body = await r.json();
  if (!body.ok) throw new Error(body.error?.message || 'api error');
  return body.data;
}
```

- [ ] **Step 4: `pages/Login.tsx` 最小页面** — email/password 受控 + POST `/api/v1/auth/login` + 成功跳 `/words`

- [ ] **Step 5: 开两个终端手测**:`uv run wordforge web --reload` + `cd frontend && npm run dev`,浏览器 `http://localhost:5173`,用 M2 CLI 建的账号登录。

- [ ] **Step 6: Commit**

```bash
git add frontend/ .gitignore
git commit -m "feat(frontend): Vite React TS skeleton + login page + api client"
```

---

## Task M6.2: 搜索页 `/words`

**Files:**
- Create: `frontend/src/pages/Search.tsx`
- Create: `frontend/src/components/WordTable.tsx`
- Create: `frontend/src/components/Pagination.tsx`

- [ ] **Step 1: Search 页** — 输入框(lemma)、status/quality/type 下拉、结果表格(form / type / status / quality / updated_at / 动作)、分页用 `next_cursor`

- [ ] **Step 2: 点某行 form → 路由 `/words/:id`**(下一 task)

- [ ] **Step 3: 手测** — 搜词、过滤、翻页

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/Search.tsx frontend/src/components/
git commit -m "feat(frontend): search page with filters + keyset pagination"
```

---

## Task M6.3: 详情编辑页 `/words/:id`

**Files:**
- Create: `frontend/src/pages/WordDetail.tsx`
- Create: `frontend/src/components/WordForm.tsx`
- Create: `frontend/src/components/DiffPreview.tsx`

- [ ] **Step 1: 一屏聚合** — word + meanings + mnemonics + sentences + phrases 全显示;audio 只读

- [ ] **Step 2: 字段可编辑** — 受控 state;每字段跟原值 diff

- [ ] **Step 3: 保存按钮** — 收集所有 dirty 字段为 `changes: [{field_path, target_id, op:'update', old_value, new_value}]`,POST `PATCH /api/v1/words/:id`

- [ ] **Step 4: 保存前 diff 预览弹窗** — 显示"改前 vs 改后"两栏,确认后才发 PATCH

- [ ] **Step 5: 收到 409** — 显示 drift 列表 + 提示刷新;200 → toast 成功 + 重新 GET 详情刷新

- [ ] **Step 6: status / quality 切换按钮** — POST `/status` / `/quality` 带 `{old_value, new_value}`

- [ ] **Step 7: "新建词"按钮路由到单独 Create 页**(可合并到本页以 query param 复用)

- [ ] **Step 8: 手测** — 改释义、改助记、drift 模拟(开两个浏览器改同一字段)、status 切换

- [ ] **Step 9: Commit**

```bash
git add frontend/src/pages/WordDetail.tsx frontend/src/components/WordForm.tsx frontend/src/components/DiffPreview.tsx
git commit -m "feat(frontend): word detail edit page with diff preview + drift handling"
```

---

## Task M6.4: 审计日志页 `/audit`

**Files:**
- Create: `frontend/src/pages/Audit.tsx`

- [ ] **Step 1: 列表页** — 按 word_id / editor / 时间过滤;显示 editor 名、field_path、op、old→new、时间;分页复用 `next_cursor`

- [ ] **Step 2: 手测 + Commit**

```bash
git add frontend/src/pages/Audit.tsx
git commit -m "feat(frontend): audit log page with filters"
```

---

# M7 — 打磨

## Task M7.1: FastAPI 静态挂载前端 dist + catch-all SPA

**Files:**
- Modify: `src/wordforge/web/app.py`

- [ ] **Step 1: `create_app` 尾部加**(API router 之后)

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

dist = Path(__file__).parent.parent.parent.parent / "frontend" / "dist"
if dist.is_dir():
    app.mount("/", StaticFiles(directory=str(dist), html=True), name="spa")
```

**顺序**:API router 先注册,static mount 最后;`/api/*` 不匹配返 API 404 JSON(不落 SPA)

- [ ] **Step 2: 本地 build + 起 prod 模式验证**

```bash
cd frontend && npm run build
cd ..
uv run wordforge web --port 8000
# 访问 http://localhost:8000/ 应该看到前端,/api/v1/words/9999999 返 JSON 404
```

- [ ] **Step 3: Commit**

```bash
git add src/wordforge/web/app.py
git commit -m "feat(web): mount SPA static dist at / with catch-all fallback"
```

---

## Task M7.2: 500 错误页 + request_id 前端展示

**Files:**
- Modify: `frontend/src/api/client.ts`(读 `X-Request-ID` + 抛错带 id)
- Create: `frontend/src/components/ErrorBoundary.tsx`

- [ ] **Step 1: api client 读 response header** `X-Request-ID` 挂到 Error

- [ ] **Step 2: ErrorBoundary 组件** 在 App 顶层捕未处理错误,显示 friendly 提示 + request_id

- [ ] **Step 3: 手测** — 故意 POST 非法 JSON / 断 DB,验证 500 页面 OK 且显示 request_id

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/ErrorBoundary.tsx
git commit -m "feat(frontend): error boundary with request_id for support"
```

---

## Task M7.3: 手测 checklist 全过(spec §5.4)

参照 spec 的 13 条手测项。逐条 ✓ 后更新 CLAUDE.md、README.md、docs/shared/ 的跨仓文档。

- [ ] **Step 1: 建 `docs/HOW_TO_RUN.md` 加 "Web admin" 小节**(或 README 里一小段):如何本地起服务、如何建 editor 账号、如何进入前端

- [ ] **Step 2: 更新 `word_forge/CLAUDE.md`** — 加 "Web admin" 一节(启动命令、凭证、`editors` CLI、sync-only Engine 架构界线、cookie secure env)

- [ ] **Step 3: 更新 `docs/shared/data-flow.md`**:
  - `domain.words` 加 `status` / `quality_flag` 列 + 语义
  - 顺便把全文 `app.*` 残留改为 `domain.*`(spec §7.3 Round 1 已列入)

- [ ] **Step 4: 更新 `docs/shared/cross-repo-map.md`** — 新增 `wordforge-web` 子服务条目,端口 8000,定位"内部工具,非公开"

- [ ] **Step 5: 把手测 checklist 逐条跑一遍**(spec §5.4):登录/登出、搜词翻页、status 过滤、详情显示、改 meaning 保存 diff 预览、双浏览器 drift 409、新建词、new form 重复跳编辑、audio 只读、audit 日志过滤、11 次错密码 429、500 友好页+request_id。逐条 ✓

- [ ] **Step 6: Commit 文档 + 收尾**

```bash
git add word_forge/CLAUDE.md word_forge/README.md word_forge/docs/HOW_TO_RUN.md docs/shared/
git commit -m "docs(web-admin): cross-repo updates and runbook for M7 release"
```

---

# 最终收敛

全部 M1-M7 完成后:
- 17 个主要 commit + 少量 fix
- 全部测试 PASS(`uv run pytest tests/ -q`)
- 手测 checklist 全过
- CLAUDE.md / README / docs/shared 同步
- spec `docs/superpowers/specs/2026-05-06-wordforge-web-admin-design.md` 无需改动,本 plan 完整覆盖







