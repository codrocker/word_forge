# wordforge web admin — design spec

> Date: 2026-05-06
> Status: draft v2 (Round 1 tri-review 修订后,进入 Round 2)
> Owner: allen

## 背景

wordforge 当前是一个纯离线 LLM 词条生产 pipeline(asyncio + sqlalchemy + alembic + CLI),数据落阿里云 RDS PG `domain.*` 下。

词条是由 LLM 批量生成的,质量参差,需要内部人工介入:搜词、改释义、改助记、标注质量、审核后才允许上线到下游 MySQL(words_core 的 `word` 库)。目前这些操作靠直连 PG 手写 SQL,效率低且危险。

本 spec 给 wordforge 加一个**内部用的 web admin**。

## 目标

- 给 3 人级别内部编辑提供一个浏览器可用的词条管理界面,替代手写 SQL 的工作流
- MVP 功能:搜词、看词全貌、手工改字段、改状态、审计日志、新建词
- 架构预留未来迭代:LLM 辅助重写、prompt/model 版本管理、权限分层

## 非目标

- 不是给 C 端用户的产品(C 端走 `sailing_words` Flutter + `words_core` Go)
- 不替代 pipeline 离线批量生成(编辑只做人工修正,不做机器产出)
- 不做批量/导入/回滚/e2e 测试等(见 §7.4 刻意 MVP 不做清单)

## 涉众与用户场景

- **内部编辑(3 人级)**:日常看"待审"队列、按 lemma 查词、改释义/助记、点"已处理"、新建漏录词
- **开发者(你)**:负责 wordforge 其它子系统,偶尔登录查词,不做大量编辑工作
- **下游消费者(words_core)**:不直接与 web 交互,只读 `domain.*` 导出到 OSS 的 JSON;web 不改 JSON 导出契约

## 与已有约束的对齐

- CLAUDE.md "结构性 DDL 走 alembic migration,不手写 SQL"
- CLAUDE.md "UPDATE 跨两个连接 = TOCTOU" → 所有编辑 + audit 同一事务
- CLAUDE.md "写入前校验 old_value" → 复用 `wordforge.reviewer.patch.PatchDriftError` 同构
- CLAUDE.md "凭证在 `~/.wordforge/`,不自建新文件" → 新增 `~/.wordforge/web.env` 承载 web 专用 secret
- CLAUDE.md "不允许吞异常(裸 `except Exception`)" → 全局 exception handler 统一分类,不吞
- docs/shared/data-flow.md "app.meanings/mnemonics/sentences 无 updated_at" → 改这三张表时不写 updated_at
- 上游 MySQL `word.word.status` 语义 `0=等待审核 / 1=已上线 / 2=已删除`(飞书 wiki 事实源) → 本次 PG 侧 `domain.words.status` 对齐该语义

## 设计流程记录

本 spec 通过 /superpowers:brainstorming 进行,与 codex + gemini 双查确认工业界主流做法;经自 review 两轮挑刺修订。待 /tri-review-battle 三方会审后定稿。

---

## Section 1 — 架构与仓内位置

### 1.1 仓内位置

后端代码放在现有 wordforge 包下新开子包:

```
src/wordforge/web/
  ├── app.py          # FastAPI app factory
  ├── deps.py         # engine / session / current_editor 依赖注入
  ├── auth.py         # 登录、password hash、session token
  ├── routes/         # words.py / audit.py / editors.py / auth.py
  ├── schemas/        # pydantic request/response models
  └── services/       # 纯业务逻辑,与 routes 解耦,可复用到 CLI
```

前端代码独立一个工程(不嵌 `src/`,不跨到外层 monorepo 的 `frontent/` —— 后者是 C 端 Flutter App,语义边界不同):

```
word_forge/frontend/
  ├── package.json    # Vite + React + TypeScript
  ├── src/
  └── dist/           # build 产物,.gitignore
```

FastAPI 在生产模式把 `frontend/dist/` 静态挂载到 `/` 路由;开发模式 Vite dev server 单独跑在 `:5173` 代理到 `:8000` API。

### 1.2 进程形态

新加 CLI 子命令 `wordforge web`(和现有 `wordforge review` 同级),内部起 uvicorn。docker-compose 加一个 service `wordforge-web`,共享同一个 PG。**不在现有离线 pipeline 进程里挂 web**,web 是独立进程,与 pipeline 严格进程隔离(避免 pipeline 长跑 LLM quota / event loop 污染 web 请求)。

### 1.3 与其它仓的关系

- web 只和 wordforge PG 通信
- **不碰** words_core / OSS / MySQL
- 不改变现有数据流:`word_forge PG → (导出脚本) → OSS → words_core` 这条线 web 不干预,只改 PG 侧的源数据

### 1.4 选型依据

codex 和 gemini 独立给出完全一致的工业界主流做法结论:
- 前端放同仓(与后端 schema 强耦合,前后端变更应能单 PR 原子闭环)
- FastAPI 走"同代码库、独立进程"(复用 sqlalchemy/alembic/domain 代码,但不嵌进 pipeline worker)

Artifacts: `.omc/artifacts/ask/codex-*.md`、`.omc/artifacts/ask/gemini-*.md`。

---

## Section 2 — 数据模型

### 2.1 修改 `domain.words` — 加两列

```sql
-- v2: DEFAULT 0 + 对已导出过的历史行 backfill 为 1(已上线)
-- 理由:75k 现有词绝大多数已经通过 quality_gate 导出到 serving/OSS/MySQL,
--       语义上是"已上线",若全部 DEFAULT 0 会让 web admin 的"待审"队列瞬间
--       堆满 75k 行,编辑体验崩溃;且与下游 MySQL 线上 status=1 不一致。
ALTER TABLE domain.words
  ADD COLUMN status SMALLINT NOT NULL DEFAULT 0
    CHECK (status IN (0, 1, 2)),
  ADD COLUMN quality_flag TEXT NOT NULL DEFAULT 'none'
    CHECK (quality_flag IN ('none','suspect','fixed'));

-- backfill:已进入 serving.word_payload(即已上线到下游)的词设为 1,其余留 0
UPDATE domain.words
   SET status = 1
 WHERE word_id IN (SELECT word_id FROM serving.word_payload);

CREATE INDEX idx_domain_words_status ON domain.words (status);
CREATE INDEX idx_domain_words_quality ON domain.words (quality_flag)
  WHERE quality_flag <> 'none';
```

**`status SMALLINT`**:对齐上游 MySQL `word.word.status` 语义 `0=等待审核 / 1=已上线 / 2=已删除`(飞书 wiki 事实源定义)。MVP **不约束流转方向**,任意切换。将来加权限分层时再限制 1→2 需 admin。

**backfill 策略**(新增 v2):migration 0011 在 ADD COLUMN 后立即 UPDATE 回填——已在 `serving.word_payload` 的词视为"已上线"(status=1),其余为 0。这和 MySQL 线上现状一致(mirror 脚本目前硬编码 status=1 也是基于这些词已上线的假设)。migration 与 backfill 是同一个 alembic upgrade 里的两步,人工跑。

**`quality_flag TEXT`**:质量标注列,`none/suspect/fixed` 三态。来源不区分(LLM reviewer 或人工都写同一列,来源从 `meta.edit_audit` 查回)。部分索引只覆盖非 none 的少数行,成本低。

### 2.2 新 schema `meta` 承载编辑侧表

不污染 `domain.*`(纯机器产出语义)/ `pipeline.*`(pipeline 执行状态)。`meta.*` 表达"编辑元数据"。

```sql
CREATE SCHEMA meta;
```

### 2.3 `meta.editors`

```sql
CREATE TABLE meta.editors (
  id            BIGSERIAL PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  display_name  TEXT NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- `email UNIQUE`:登录账号,3 人规模不做 username 抽象
- `password_hash`:argon2-cffi 默认参数产出
- `is_active`:软禁用;不做硬删(audit 外键要求)
- 不加 `last_login_at / failed_login_count`:YAGNI;失败限速用应用层 rate limit,不持久化

### 2.4 `meta.editor_sessions`

```sql
CREATE TABLE meta.editor_sessions (
  token_hash    TEXT PRIMARY KEY,
  editor_id     BIGINT NOT NULL REFERENCES meta.editors(id) ON DELETE CASCADE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL
);
CREATE INDEX idx_editor_sessions_editor ON meta.editor_sessions (editor_id);
```

- `token_hash` PK:存 `sha256(raw_token)`,raw token 只在 cookie,不进 DB。DB 泄漏场景攻击者拿到的是 hash 不能直接用
- `ON DELETE CASCADE`:editor 软禁用不触发;若未来做硬删(MVP 不允许),session 自动清
- 踢会话直接 `DELETE` 该行;不单独存 `revoked_at`

### 2.5 `meta.edit_audit`

```sql
CREATE TABLE meta.edit_audit (
  id            BIGSERIAL PRIMARY KEY,
  word_id       BIGINT NOT NULL,
  field_path    TEXT NOT NULL,
  op            TEXT NOT NULL CHECK (op IN ('update','insert','delete')),
  old_value     JSONB,
  new_value     JSONB,
  editor_id     BIGINT NOT NULL REFERENCES meta.editors(id) ON DELETE RESTRICT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_edit_audit_word ON meta.edit_audit (word_id, created_at DESC);
CREATE INDEX idx_edit_audit_editor ON meta.edit_audit (editor_id, created_at DESC);
```

- `word_id` **不加 FK**:允许 word 硬删时 audit 保留,防"失忆式注销"——哪天误删 word,审计历史不跟着消失;追溯被删词可 JOIN `old_value->'form'`
- `editor_id` `ON DELETE RESTRICT`:删 editor 前 audit 必须空;正确做法是 `is_active=false` 软禁用
- 不加 `session_id / request_id / ip_address / user_agent / reason`:YAGNI,痛了再加

### 2.6 alembic 迁移

- 迁移文件 `0011_add_editor_workflow.py`,续接现有命名
- 一次 migration 包含:
  1. `domain.words` 扩列(status / quality_flag)
  2. **backfill**:已在 `serving.word_payload` 的词 status=1,其余留 0(§2.1)
  3. 新 `meta` schema + 三张表 + 索引
- **web 容器启动不碰 alembic**,部署前人工跑 `alembic upgrade head`(见 §6)

---

## Section 3 — API 契约

### 3.0 共同规约

- 前缀 `/api/v1/*`
- 所有响应统一 envelope:
  ```json
  // 成功
  { "ok": true, "data": <payload>, "error": null }
  // 失败
  { "ok": false, "data": null, "error": {"code": "...", "message": "...", "details": {}} }
  ```
- 鉴权走 **httpOnly cookie**(SameSite=Strict),不用 bearer token:SPA 自动带,天然防 CSRF
- 所有写操作在**同一个 `engine.begin()` 事务**里完成 domain 修改 + audit 写入
- 字段命名 snake_case,前后端共用,不做 camelCase 转换
- 时间 ISO-8601 UTC:`"2026-05-06T10:30:00Z"`
- 每次请求由 middleware 赋 `request_id = uuid4()` + 响应头 `X-Request-ID`,所有服务端日志带该 ID

### 3.1 Auth

```
POST /api/v1/auth/login
  body  {email, password}
  resp  201 Created
        data: {editor: {id, email, display_name}}
        cookie: Set-Cookie: session=<raw_token>; HttpOnly; SameSite=Strict; Path=/api; Max-Age=604800; Secure (prod)
  err   401 unauthenticated (邮箱或密码错)
        429 rate_limited (IP 限速 10/60s)

POST /api/v1/auth/logout
  resp  200 OK
        data: null
        副作用: DELETE FROM meta.editor_sessions WHERE token_hash = sha256(cookie)

GET  /api/v1/auth/me
  resp  200 OK
        data: {id, email, display_name}
  err   401 unauthenticated
```

**rate limit**:`slowapi` middleware,`@limiter.limit("10/60seconds")` 挂在 login endpoint,按 IP 限。

### 3.2 Words 搜索

```
GET /api/v1/words
  query:
    q          ?  lemma 子串搜(ILIKE '%q%')
    status     ?  0|1|2 过滤
    quality    ?  none|suspect|fixed 过滤
    type       ?  1|2 过滤
    pos        ?  1-10|201 过滤
    order      ?  updated_at_desc (MVP 唯一)  -- lemma_asc 后续迭代
    cursor     ?  keyset 游标(见下)
    limit      ?  默认 50,最大 200
  resp  {ok, data: {items: [...], next_cursor}}
```

每条 item 形态:
```json
{"word_id": 12345, "form": "serendipity", "type": 1, "status": 0,
 "quality_flag": "suspect", "updated_at": "2026-05-06T10:30:00Z",
 "meaning_count": 3}
```

**分页**(v2 简化):keyset 游标 = `(updated_at, word_id)`,**纯 base64(JSON),不签名**。

```
cursor = base64(json({o: "updated_at_desc", u: "2026-05-06T10:30:00Z", w: 12345}))
```

**为什么不签名**(Round 1 砍):3 人内网场景下,篡改游标无越权利得——所有登录 editor 都有满权,游标只决定翻页位置。HMAC 签名引入了 `WORDFORGE_WEB_SECRET_KEY` 这个新 secret + 新 env 文件 + 轮换问题,同时会违反 CLAUDE.md "不自建新凭证文件" 硬规矩。**砍**。

**cursor 内含 order 字段**(Round 1 修):cursor 必须记录当前 order 模式(`updated_at_desc` / `lemma_asc`),因为不同 order 需要不同的 keyset 起点(`lemma_asc` 下是 `(form, word_id)`,不是 `updated_at`)。用 mismatched cursor 请求视为 400 `invalid_input`。

**MVP order 锁定**(Round 1 简化):MVP 只实现 `updated_at_desc` 一种。`lemma_asc` 留到后续迭代。cursor schema 已预留 order 字段,将来扩展零摩擦。

**decode 失败**:返 400 `invalid_input`,不静默跳到第一页(避免结果诡异)。

**搜索 scope**:MVP 只对 `form` 做 ILIKE,不对释义/例句全文搜。75k 词规模下 ILIKE 扫得动,痛了再加 pg_trgm 索引。

### 3.3 Word 详情(一屏全貌)

```
GET /api/v1/words/{word_id}
  resp  {ok, data: {
    word:      {word_id, form, type, phonetic_us, phonetic_uk, audio_us, audio_uk,
                status, quality_flag, source, created_at, updated_at, plural,
                past_tense, past_participle, third_person, present_participle,
                comparative, superlative, derivatives, structure},
    meanings:  [{meaning_id, pos, pos_sub, cn_paraphrase, en_paraphrase,
                 equivalents, synonyms, antonyms, source, created_at}],
    mnemonics: [{mnemonic_id, type, content, source, created_at}],
    sentences: [{sentence_id, meaning_id, form, translation, highlight,
                 citation, citation_detail, source, created_at}],
    phrases:   [...]
  }}
  err   404 not_found
```

一次请求聚合所有关联,前端不跳多页。

**audio_us / audio_uk**:展示只读。MVP 不让编辑改音频(改 audio 需要文件上传,复杂度另一档,YAGNI)。

### 3.4 PATCH 保存编辑(核心写路径)

```
PATCH /api/v1/words/{word_id}
  body  {
    changes: [
      {field_path: "words.form",            target_id: null,  op: "update",
       old_value: "...", new_value: "..."},
      {field_path: "meanings.cn_paraphrase", target_id: 12345, op: "update",
       old_value: "...", new_value: "..."},
      {field_path: "mnemonics.content",     target_id: 67890, op: "update",
       old_value: {...}, new_value: {...}}
    ]
  }
  resp  200 OK data: {applied: N}
  err   409 conflict data: {drift: [{field_path, target_id, db_value, expected_old_value}]}
        404 not_found
```

**关键设计**:

1. **显式 ID 寻址**:`field_path` 只声明"改哪张表哪个列",具体哪行由 `target_id` 指定(meaning_id/mnemonic_id/sentence_id)。不用 `meanings[3]` 数组索引——索引会因并发 insert/delete 错位
2. **drift 策略:严格 rollback**:任一条 change 的 `old_value` 与 DB 当前值不符,整个事务 rollback 返 409,前端必须刷新重来。与 reviewer 批量跑"部分成功"策略不同——人的心理模型是"我看到的版本就是要改的版本",部分成功会让编辑误以为保存了
3. **复用 reviewer.patch 的 low-level primitives,不复用整函数**(Round 1 修):
   - `wordforge.reviewer.patch.apply_patches_for_word` 是 **skip-on-drift** 语义(机器批跑部分成功可接受);web 场景要的是 **all-or-nothing**(人心理模型要求"看到的版本就是要改的版本")
   - 两者语义不兼容,**不能直接复用整函数**
   - 正确做法:web service 层新建 `wordforge.web.services.word_service.apply_web_changes(conn, word_id, changes)`,内部调用 `reviewer.patch.check_drift()` + `reviewer.patch.apply_patch()` 两个低层原语;**第一个 drift 处立即 raise `PatchDriftError`**,由外层 `engine.begin()` 上下文自动回滚整事务
   - `PatchDriftError` 冒到全局 exception handler → 409 `conflict`,response body 含 drift 列表(哪些 changes 与 DB 不符)
4. **原子审计写入**:每条成功 change 在同事务内写一条 `meta.edit_audit`;任一方失败全回滚
5. **serving.word_payload 同事务 rebuild**(Round 1 新增,P1 fix):
   - wordforge 现有 export stage 在改动 `domain.*` 后**同事务**调用 `_upsert_serving_word_payload(conn, word_id)` 重建下游读模型;web PATCH 若跳过,编辑结果不会进 `serving.word_payload`,下游 words_core 读到的是 stale 数据
   - 实施:把 `wordforge.stages.export.ExportStage._upsert_serving_word_payload` 提取为公共函数 `wordforge.db.serving.rebuild_word_payload(conn, word_id)`,export + web service 共用
   - web service 在 PATCH 事务末尾、POST 成功新建后、status/quality 切换后都调用一次
6. **updated_at 规矩**(对齐 CLAUDE.md "数据模型地雷"):改 `domain.words` 时带 `updated_at = now()`;改 `meanings/mnemonics/sentences` 三张无 updated_at 列,不写

### 3.5 状态切换

```
POST /api/v1/words/{word_id}/status
  body  {old_value: 0|1|2, new_value: 0|1|2}
  resp  200 OK data: null
  err   409 conflict data: {db_value, expected_old_value}  -- drift

POST /api/v1/words/{word_id}/quality
  body  {old_value: "none"|"suspect"|"fixed", new_value: "none"|"suspect"|"fixed"}
  resp  200 OK data: null
  err   409 conflict data: {db_value, expected_old_value}  -- drift
```

**v2 加 old_value drift 校验**(Round 1 修,P1):对齐 CLAUDE.md 硬规矩 #2 "写入前必须校验 old_value"。前端在 GET 详情时拿到当前 `status/quality_flag`,改动时把原值作为 `old_value` 回传,service 层比对 DB 当前值——不匹配 raise `PatchDriftError` → 409,和 PATCH 同构。不能只发 `{status: 1}` 黑盒覆盖,会造成并发编辑时后者静默覆盖前者。

**独立 endpoint 原因**:语义是"工作流动作"而非"字段编辑",audit 单独记 `field_path='words.status'` / `'words.quality_flag'`,`op='update'`,`old_value`/`new_value` 填真实值。**service 层**与 PATCH 共用同一套 `apply_web_changes` + audit 写入 + serving rebuild 路径,不手写独立 audit。

**`suspect → fixed` 由编辑手动点按钮触发**,不在 PATCH 里自动转换。

### 3.6 新建词

```
POST /api/v1/words
  body  {
    form:        "serendipity",
    type:        1,                 -- 必填,1=word / 2=phrase
    phonetic_us: "...",             -- 可选
    phonetic_uk: "...",             -- 可选
    meanings:    [...],             -- 可选,[] 也 OK
    mnemonics:   [...],             -- 可选
    sentences:   [...],             -- 可选
    phrases:     [...]              -- 可选
  }
  resp  201 Created data: {word_id, created: true}
  err   409 conflict data: {word_id: <existing>, created: false, reason: "already_exists"}
        400 invalid_input
```

- `form` 规范化:`strip()` 去两端空白,**不 lowercase**(phrase 大小写有意义)
- 新建词 `source = 'human:web'`(符合 domain.words CHECK 约束)
- **子表 source 服务端强制 stamp**(Round 1 修,P1):`meanings / mnemonics / sentences / phrases` 每张子表都有 `source` NOT NULL + CHECK(`pipeline:% | human:% | import:%`)。新建走 web 路径时,**所有子行**的 `source` 都由 service 层强制填 `'human:web'`,**忽略**前端 body 里任何 `source` 字段(即使前端塞了也不用)。避免前端误传 `'pipeline:xxx'` 污染来源追溯 + 避免 CHECK 约束失败
- status 默认 `0`(等待审核),quality_flag 默认 `'none'`
- **form+type 冲突降级为编辑**:先查存在性,存在 → 409 + 返 `word_id`,前端路由到 `/words/{id}` 编辑页
- 并发 UNIQUE 碰撞:service 捕 `IntegrityError` → 查已存在的 word_id → 走同一个 409 响应路径
- audit:创建走 `op='insert'`,每张表每行一条
- **同事务 rebuild serving.word_payload**(Round 1 新增):POST 成功后调 `rebuild_word_payload(conn, new_word_id)`

### 3.7 审计日志

```
GET /api/v1/audit
  query:
    word_id    ?  某词的历史
    editor_id  ?  某编辑的历史
    since      ?  起始时间
    until      ?  截止时间
    cursor     ?  keyset 游标
    limit      ?  默认 100,最大 500
  resp  {ok, data: {items: [{id, word_id, field_path, op,
                              old_value, new_value,
                              editor: {id, display_name},
                              created_at}],
                    next_cursor}}
```

MVP 只做查询,不做"点一条 audit 就回滚"。audit 数据结构足够将来实现回滚。

---

## Section 4 — Auth + Error Handling + 前端交互契约

### 4.1 Auth 实现

**密码存储**:
- `argon2-cffi`(passlib 默认后端),默认参数 `time_cost=3, memory_cost=64*1024, parallelism=4`
- 永不记录明文密码(日志/审计/内部表均不存)
- **argon2 verify/hash ~100ms CPU 阻塞**(Round 1 修,P1):web 的路由处理函数**统一声明为 sync `def`**(不是 `async def`),由 FastAPI/Starlette 自动调度到 threadpool。这与 wordforge 现有 sync `Engine` 匹配,避免 async-sync 混用 + 天然让 argon2/DB I/O 不阻塞 event loop
- 不在 sync 路由里套 `asyncio.to_thread`(冗余);在 async 路由里必须 `await asyncio.to_thread(ph.verify, ...)`(本 MVP 不走此路径)

**session token**:
- 签发:`raw = secrets.token_urlsafe(32)` → `token_hash = sha256(raw)` 存 DB → `raw` 塞 cookie
- 校验:cookie 拿 raw → sha256 → 查 `meta.editor_sessions WHERE token_hash = ? AND expires_at > now()`
- 登出:`DELETE FROM meta.editor_sessions WHERE token_hash = sha256(cookie)`

**cookie 规约**(v2 明确):
```
Set-Cookie: session=<raw_token>; HttpOnly; SameSite=Strict; Path=/api; Max-Age=604800; [Secure]
```
- `Max-Age = 7 * 24 * 3600`(7 天),同步 `expires_at`
- `Secure` **不无脑开**(Round 1 修,P1):由 env `WORDFORGE_WEB_COOKIE_SECURE` 控制
  - MVP 部署模式是"本机 docker + 内网 HTTP / SSH tunnel 到 localhost",**HTTP 场景 browser 会拒收 Secure cookie** → 登录直接失效
  - 默认 `WORDFORGE_WEB_COOKIE_SECURE=false`(适配内网 HTTP)
  - 未来云部署加 TLS 时改 `=true`
  - **部署文档必须显式说明这个 env 的选择规则**
  - 这个 env 不是 secret(bool),可以放 docker-compose `environment` 块,不污染 `~/.wordforge/` 凭证体系

**登录保护**:slowapi `@limiter.limit("10/60seconds")` 按 IP,超限 429。

**账户创建走 CLI,不暴露 web**:
```
wordforge editors create --email xxx --display-name xxx   # stdin 收密码
wordforge editors list
wordforge editors deactivate --email xxx
```
对齐 CLAUDE.md "初始化走 CLI,日常走 web"。未来加权限分层时再开 web 账号管理 API。

### 4.2 Error envelope

统一 envelope:`{ok, data, error}`,error 形如 `{code, message, details}`。

错误码分类(扁平):

| HTTP | code | 场景 |
|---|---|---|
| 400 | `invalid_input` | pydantic 校验失败,`details` 带字段级错误 |
| 401 | `unauthenticated` | cookie 缺失 / 过期 / token_hash 找不到 |
| 403 | `forbidden` | 登录后无权限(MVP 基本用不到,权限分层后激活) |
| 404 | `not_found` | word_id / audit id 不存在 |
| 409 | `conflict` | PATCH drift / POST /words form+type 重复 |
| 429 | `rate_limited` | 登录超频 |
| 500 | `internal` | 未捕获异常,不回显 stack trace |

**异常处理规矩**(对齐 CLAUDE.md "不允许吞异常"):
- FastAPI 全局 exception handler 统一分类
- `HTTPException` → 按 status_code 映射
- `IntegrityError` → 409
- `PatchDriftError`(复用 reviewer.patch) → 409 + drift 列表
- 兜底 `Exception` → 500 + `logger.exception`(带 request_id)
- **禁止** service 层裸 `except Exception`——要么重塑为业务异常,要么让它冒到全局 handler
- 5xx 响应永远返固定消息"系统错误,已记录,请稍后重试"+ request_id;stack trace 只进日志

### 4.3 前端交互契约

**字段命名**:后端/响应全 snake_case,前端直接写 `data.word_id`,不做转换。

**时间**:后端 ISO-8601 UTC,前端用 dayjs 转本地时区展示。

**VITE_ 前缀的安全规矩**:
- `VITE_API_BASE` 等只放非敏感运行时配置
- **禁止**把任何 secret(session key / argon2 pepper / DB DSN)塞前端 env,哪怕带 VITE_ 前缀
- 新开发加 env 时按规则:`VITE_*` 进前端 bundle,默认非 VITE_* 不会被 Vite 打包,天然安全

**API base URL**:
- dev:Vite `proxy` 把 `/api` 转发到 `:8000`
- prod:FastAPI 静态挂前端,同源,直接 `/api/v1`

### 4.4 CORS

- prod:同源,不需要 CORS
- dev:FastAPI 开 CORS middleware 白名单 `http://localhost:5173`(仅 dev)

### 4.5 连接池 + Engine 类型锁定

**Engine 构造**(Round 1 修,P1):
- web 进程**独立**调用 `wordforge.db.engine.make_engine(url, pool_size=5, max_overflow=5)` 创建自己的 `Engine` 实例(不共享 pipeline 的)
- **只用 sync `Engine`**,**不引入 `AsyncEngine` / `create_async_engine`**,对齐 CLAUDE.md sync 路径 + 已有 `reviewer.patch` 等模块同构
- 所有 route handler 声明为 sync `def`,FastAPI 自动调度到 threadpool(见 §4.1)
- 所有 service 方法 sync,直接 `with engine.begin() as conn:`,不包 `asyncio.to_thread`

**连接池规模**:
- pipeline 进程:长任务,池大 ~20
- web 进程:请求短,池 `pool_size=5, max_overflow=5`
- 两进程独立 pool 连同一 RDS;serverless 最大连接数足够,无冲突

### 4.6 pytest env 污染规矩

新加 `WORDFORGE_WEB_SECRET_KEY` 后,必须同步加入 `tests/test_cli.py::_LLM_PROVIDER_ENV_KEYS`(或同等 env-pop list),防止环境泄漏污染"无 LLM 路径"测试——对齐 CLAUDE.md 硬规矩。

---

## Section 5 — 测试策略

### 5.1 分层

**单元测试(`tests/web/unit/`)**:
- `auth.py` password hash / token 生成/校验
- `services/word_service.py` patch 落库(mock engine,验证事务边界)
- `services/audit_service.py` audit 写入格式
- cursor encode/decode + HMAC 签名校验
- pydantic schemas 拒绝非法输入

**集成测试(`tests/web/integration/`)—— 主战场**:
用 FastAPI `TestClient` + 真 PG(test db 5434,`tests/conftest.py` guard 必须通过)。

**必须覆盖的场景**(v2 扩充):
```
[] 登录 → cookie 带入 → 访问 /me → 登出 → cookie 失效
[] 搜词:lemma 子串 / status 过滤 / 分页 keyset + next_cursor 往返
[] 详情页聚合正确(word + meanings + mnemonics + sentences + phrases)
[] PATCH 成功路径,applied 数量正确
[] PATCH drift:故意让 old_value 不匹配 → 409 + 整事务 rollback + audit 无记录
[] PATCH 原子性-A:模拟 domain UPDATE 抛错 → 事务回滚 → audit 无记录 + serving 未改
[] PATCH 原子性-B:模拟 audit INSERT 抛错 → 事务回滚 → domain 未改 + serving 未改
[] PATCH 原子性-C(v2 新):模拟 serving rebuild 抛错 → 事务回滚 → domain 未改 + audit 未写
[] PATCH 成功后 serving.word_payload 内容与 domain 最新值一致(v2 新)
[] POST /words 新建成功 → 201 + audit 有 insert 记录(每张子表一条)+ serving rebuild 完成
[] POST /words 新建:body 中子表 source='pipeline:xxx' 被服务端覆盖为 'human:web'(v2 新)
[] POST /words form+type 重复 → 409 + 返已存在 word_id
[] POST /words 并发 UNIQUE 碰撞 → 第二个请求走 409 同一路径
[] POST /status drift:old_value 与 DB 不符 → 409 + 无副作用(v2 新)
[] POST /quality drift:同上(v2 新)
[] POST /status 成功后 audit 记录 field_path='words.status',old/new 值正确
[] 审计日志按 word_id / editor_id / 时间过滤
[] 未登录访问受保护 endpoint → 401
[] Rate limit:11 次错密码后第 11 次 → 429
[] domain.meanings/mnemonics/sentences 改动 SQL 不含 updated_at
[] cursor 传入错误 order(与当前 query order 不符)→ 400 invalid_input(v2 新)
[] cursor base64 解码失败 → 400 invalid_input(v2 新)
```

**不做 e2e**(Playwright/Cypress)。MVP 手测 checklist 兜底。

### 5.2 测试数据

- 不引 factory_boy 等重库,`tests/web/conftest.py` 提供 `make_editor() / make_word() / make_meaning()` 简单 helper
- 复用 `tests/conftest.py` 的 test-db guard(localhost + 名字含 test 才跑,否则 `pytest.exit()`)
- 每类 teardown: TRUNCATE `meta.*` + 影响到的 domain 行

### 5.3 前端测试

- Vitest + React Testing Library,覆盖关键组件:搜索过滤、编辑表单受控、diff 预览
- 不做 Storybook(组件数 <20)
- 不做视觉回归

### 5.4 手测 checklist(MVP 发布前必过)

```
[] 登录/登出/cookie 7 天有效
[] 搜 lemma,分页翻 5 页能回第 1 页
[] status 过滤 0/1/2,总数对得上
[] quality 过滤 suspect/fixed/none
[] 点进某词,所有字段正确显示
[] 改 cn_paraphrase 保存,diff 预览正确
[] 双浏览器改同字段:后保存的 409 + 刷新重来
[] 新建 form='test_new',type=1 → 201 + 跳编辑页
[] 新建已存在 form → 409 + 跳编辑页
[] audio 字段只读
[] 审计日志按词/编辑/时间筛
[] 连续 11 次错密码 → 第 11 次 429
[] 500 页面友好(不漏 stack trace)+ 展示 request_id
```

### 5.5 CI

- `uv run pytest tests/web/ -q`(复用现 pytest env guard)
- 前端 `cd frontend && npm test -- --run`(Vitest non-watch)
- 不新搭 GH Actions,本地跑过即 OK,对齐 wordforge 现有风格

---

## Section 6 — 部署与运行

### 6.1 开发模式

```bash
# 后端(终端 1)
cd word_forge
uv run wordforge web --reload --port 8000

# 前端(终端 2)
cd word_forge/frontend
npm run dev     # Vite :5173,proxy /api → :8000
```

`wordforge web` 是 thin wrapper,透传 `--host / --port / --reload / --workers` 到 `uvicorn.run()`,不自己重做参数解析。

### 6.2 生产模式(本机 docker + 内网访问)

```bash
cd word_forge/frontend && npm run build
cd ..
# 先人工跑 migrate(容器不碰 alembic)
# v2:必须显式 source prod.env,不能依赖 shell 现有环境
set -a
source ~/.wordforge/prod.env
set +a
# 校验 target(避免误跑 test 或错的 DB)
echo "migrate target: $DATABASE_URL" | grep -E 'wordforge[^_]' || { echo "WRONG DB"; exit 1; }
uv run alembic upgrade head
# 起 web
docker compose up -d wordforge-web
```

FastAPI 启动时静态挂 `frontend/dist/`,SPA 路由走 catch-all `index.html`。

**挂载顺序**(Round 1 修):`app.include_router(api_router, prefix='/api/v1')` 先注册,最后 `app.mount('/', StaticFiles(directory='frontend/dist', html=True))` 兜底。`/api/*` 不匹配的路径由 API exception handler 返 JSON 404,不落入 SPA catch-all。

**部署范围限定**:MVP 仅覆盖"本机 docker + 内网/VPN/SSH 隧道访问"。云部署(远端服务器凭证分发、Nginx/TLS)TBD,不在 MVP 内。

### 6.3 docker-compose

新增 service:

```yaml
wordforge-web:
  build:
    context: .
    dockerfile: Dockerfile.web
  environment:
    # v2:最小权限原则 — 只 pass 必要变量,不挂整份 prod.env
    # 这避免 web 容器拿到 AWS_* / OPENAI_API_KEY 等 LLM 凭证
    # 宿主先 source ~/.wordforge/prod.env 再 up,下面的 ${VAR} 从宿主 env 注入
    DATABASE_URL: "${DATABASE_URL}"
    WORDFORGE_WEB_COOKIE_SECURE: "false"   # 内网 HTTP 模式;TLS 部署时改 true
  ports:
    - "8000:8000"
  restart: unless-stopped
  # 不加 depends_on: wordforge-pg — DB 是 RDS(外部),不是 docker-compose 里的 pg 容器
```

**v2 凭证最小化**(Round 1 修,P2 #9):
- 不用 `env_file: ~/.wordforge/prod.env`(两重风险:一是 `~` 在 docker-compose YAML 里展开不稳定;二是把 AWS/LLM 凭证全透给 web 容器违反最小权限)
- 改用 `environment:` 块 + 宿主 shell 的 `${VAR}` 插值——宿主 `source ~/.wordforge/prod.env` 后 `docker compose up`,只把 allowlist 里的变量注入容器
- 对齐 CLAUDE.md "不自建新凭证文件" 硬规矩——MVP 零新增凭证文件(原计划的 `web.env` 已砍)

**docker-compose 启动流程**:
```bash
set -a; source ~/.wordforge/prod.env; set +a
docker compose up -d wordforge-web
```

### 6.4 凭证策略(v2 零新增文件)

**Round 1 砍**:原计划的 `~/.wordforge/web.env` + `WORDFORGE_WEB_SECRET_KEY` 已整体砍掉(cursor HMAC 过度 + 违反"不自建新凭证文件"硬规矩)。

MVP web 进程只需:
- `DATABASE_URL`(已在 `~/.wordforge/prod.env`,宿主 `source` 后 docker-compose 注入)
- `WORDFORGE_WEB_COOKIE_SECURE`(bool,非 secret,直接写 docker-compose `environment` 块)

**对齐 CLAUDE.md 硬规矩**:web MVP 零新增凭证文件、零新增 env 扩展到 `~/.wordforge/`。未来加 secret(JWT signing / CSRF double-submit)时再补。

### 6.5 Dockerfile.web(独立,不复用 pipeline 的)

多阶段 build:
- Stage 1:`node:22-alpine` build 前端,产出 `dist/`
- Stage 2:`python:3.12-slim` `uv sync --extra web`(新加 extra)
- Stage 3:copy stage1 `dist/` + stage2 `.venv/`,ENTRYPOINT `wordforge web --host 0.0.0.0 --port 8000`

**`pyproject.toml [web]` extra 必须定义**(Round 1 修,P2):
```toml
[project.optional-dependencies]
web = [
    "fastapi>=0.111",
    "uvicorn[standard]>=0.29",
    "argon2-cffi>=23.1",
    "slowapi>=0.1.9",
    "python-multipart>=0.0.7",   # form parsing for login
]
```
与现有 `dev` / `llm` extra 并列。`uv sync --extra web --extra dev` 可本地联调。

### 6.6 CLI 子命令

`src/wordforge/cli.py` 增:
```
wordforge web [--port 8000] [--host 0.0.0.0] [--reload] [--workers N]

wordforge editors create --email xxx --display-name xxx   # stdin 收密码
wordforge editors list
wordforge editors deactivate --email xxx
```

session cleanup 兜底(opportunistic):login endpoint 内顺手删除过期 session(`DELETE WHERE expires_at < now()`),无需 cron。

### 6.7 migration 策略

- web 容器启动**不**跑 alembic
- 发布流程:人工 `alembic upgrade head` → 验证 → 起 web 容器
- 理由:避免多实例并发 migrate 踩坑;人工可控;对齐 CLAUDE.md "结构性 DDL 走 alembic migration" 规矩

---

## Section 7 — 实现 Roadmap + 风险 + 跨仓文档

### 7.1 MVP 里程碑

| 阶段 | 范围 | 验收 |
|---|---|---|
| M1 基础设施 | alembic 0011 + `wordforge.web` 包骨架 + `wordforge web` CLI + docker service + `editors` CLI | 本地起服务 + CLI 建 editor |
| M2 auth | login/logout/me + session 表 + cookie + argon2 + rate limit | 登录 + 7 天 cookie + 错密码限速 |
| M3 只读 API | 搜词 + 详情 + audit 查询 + keyset cursor + HMAC | curl 能搜 + 看词全貌 |
| M4 编辑写路径 | PATCH + drift rollback + audit 原子 + status/quality | 能改词 + drift 返 409 + audit 对 |
| M5 新建词 | POST /words + UNIQUE 降级编辑 | 新建或跳编辑 |
| M6 前端 SPA | Vite+React 工程 + 登录页 + 搜索页 + 详情编辑页 + 审计页 | UI 通手测 checklist |
| M7 打磨 | 500 页 + request_id 展示 + 部署文档 + 手测全过 | 发给内部试用 |

**依赖**:M1→M2→M3→(M4,M5 并行)→M6→M7。M6 前端可在 M3 ready 时开干(先只读 UI)。

### 7.2 风险表

| 风险 | 影响 | 缓解 |
|---|---|---|
| PATCH drift 频发 | 改完要刷新 | MVP 接受(3 人并发极低);后续可加字段级锁或 last-updated-by 展示 |
| argon2 慢 | 登录 >1s | passlib 默认 ≈100ms,不是问题;否则降 memory_cost |
| 前后端契约漂移 | 字段对不齐 | 短期:手测 checklist + code review;中期:openapi → ts types(非 MVP) |
| 并发 UNIQUE 碰撞 | 两人同时新建同词 | service 捕 IntegrityError 转 409(Section 3.6) |
| `~/.wordforge/` 挂载 | 远端拿不到凭证 | MVP 限定本机 docker;云部署 TBD |
| session 表膨胀 | 持续登累积行 | login 时 opportunistic `DELETE WHERE expires_at < now()` |
| pipeline + web 共用 PG | 连接数紧张 | 独立池 + RDS serverless 连接数足够 |
| Semi Design 将来切换 | 重构成本 | 组件浅封装 + 无 form 库绑定,切换为"找替换"级而非重写 |

### 7.3 跨仓文档更新计划

按 CLAUDE.md "跨仓改动同步更新 docs/shared/" 规矩:

| 文件 | 更新内容 | 触发 |
|---|---|---|
| `docs/shared/cross-repo-map.md` | 新增 `wordforge-web` 作为 wordforge 子服务,端口 8000,定位"内部工具,非公开" | M1 落地 |
| `docs/shared/data-flow.md` | (1) `domain.words` 新增 `status` + `quality_flag` 两列 + 语义(status 对齐上游 MySQL 三态码) (2) 顺便把全文残留的 `app.*` 引用更新为 `domain.*`(migration 0007 改名已过 4 天还没同步,趁此一次修完 — Round 1 修 P3) | M1 migration 落地 |
| `word_forge/CLAUDE.md` | 新增 "Web admin" 小节(启动命令 / sync-only Engine 架构界线 / `editors` CLI / web cookie Secure 环境开关) | M1/M2 落地 |
| `word_forge/README.md` | "本地 Postgres 的定位"后加一小节 "Web Admin" | M7 发布 |
| **`scripts/replicate/field_mapping.py`**(Round 1 新增 P1) | `row_to_mysql_word()` 改读 PG `domain.words.status` 列(目前硬编码 `"status": 1`)。Migration 0011 落地后,mirror 脚本若不同步改,会把所有词都强制写 MySQL status=1,丢掉 web admin 改动 | M1 migration 落地,与 migration 同 PR |
| **`scripts/replicate/mirror_to_mysql.py`**(Round 1 新增 P1) | 同上:调用点改用新字段 | 同上 |
| 飞书 wiki | **不更新** — wiki 只管 MySQL 上游事实源,PG domain.* 不进 wiki |

### 7.4 MVP 刻意不做清单

- 批量编辑(勾多词同改)
- 回滚/撤销 UI(audit 数据结构够将来实现)
- 标签/批注(TODO marks)
- 只读角色(MVP 登录即满权)
- LLM 辅助重写(明确下一阶段)
- Prompt / model 版本管理
- CSV 批量导入
- 改 audio 文件
- 全文搜释义/例句
- Web 里建/改 editor 账号
- CSRF double-submit(SameSite=Strict 已兜底)
- openapi → ts types 自动生成
- Playwright/Cypress e2e
- 多实例水平扩展
- 云部署凭证分发
- **cursor HMAC 签名**(Round 1 砍):3 人内网无攻击面收益;未来公网暴露再加
- **`lemma_asc` order**(Round 1 收紧):MVP 只实现 `updated_at_desc`;cursor schema 预留 order 字段,未来扩展无摩擦
- **Async Engine / async def 路由**(Round 1 锁定):sync-only 路径,对齐 sqlalchemy 2.0 sync Engine

### 7.5 决策源摘要

- codex + gemini 独立双查确认:前端放同仓 + FastAPI 同代码库独立进程。artifacts `.omc/artifacts/ask/`
- `domain.words.status` 语义对齐飞书 wiki `word.word.status` 三态码(原文见 2026-05-06 查询,`https://lpt2q1lbzh.feishu.cn/wiki/wikcnQFiS6CvAj8sfXW86mK1d2G`)
- 与用户对话中明确敲定的 MVP 范围:搜词 + 手工改 + 审计 + 新建词 + 状态/质量切换;将来扩到 LLM 辅助重写、prompt/model 管理

---

## 附录 A — Round 1 Tri-Review 决策映射(v1 → v2)

Round 1 发现,三方独立 review 后的修订清单(每条带源方和文档落点):

| # | Severity | 来源 | 问题摘要 | 修订落点 |
|---|---|---|---|---|
| F1 | P1 | architect | `apply_patches_for_word` skip-on-drift 与 web all-or-nothing 语义不兼容 | §3.4 point 3 改为"复用 low-level primitives" |
| F2 | P1 | architect | web PATCH 未同步 `serving.word_payload` → 下游 stale | §3.4 point 5 新增;§3.6 POST 同;测试清单 3 条 |
| F3 | P1 | architect | `mirror_to_mysql.py` hardcode `status=1` | §7.3 新增两行 |
| C1 | P1 | architect + codex + gemini | cursor HMAC 过度 + `web.env` 违反凭证硬规矩 | §3.2 砍 HMAC;§6.4 完全重写;§7.4 加入不做清单 |
| C2 | P1 | architect + codex + gemini | argon2 阻塞 event loop,async/sync 策略不明 | §4.1 补 sync def 选择;§4.5 锁死 sync Engine |
| C3 | P1 | architect + codex | status/quality endpoint 缺 old_value drift | §3.5 body 加 `old_value/new_value` + drift 409 |
| C4 | P2 | architect + codex | status DEFAULT 0 对 75k 现行无 backfill | §2.1 加 `UPDATE ... WHERE word_id IN serving.word_payload`;§2.6 明示 |
| C5 | P1 | architect + gemini | Sync Engine + FastAPI 路由签名未锁 | §4.5 重写 "Engine 类型锁定" |
| U4 | P1 | codex | `Secure` cookie 在 HTTP 内网部署失效 | §4.1 cookie 规约加 `WORDFORGE_WEB_COOKIE_SECURE` env |
| U5 | P1 | codex | POST /words 子表 source 未 stamp | §3.6 service 强制填 `human:web` 覆盖前端 |
| U6 | P2 | codex | cursor 对 lemma_asc 不兼容 | §3.2 MVP 锁 updated_at_desc;cursor 内含 order |
| U7 | P2 | codex | migration 命令未 source env | §6.2 加 `set -a; source; set +a` + 校验 |
| U8 | P2 | codex | docker-compose `~` 展开 + `depends_on: wordforge-pg` 矛盾 RDS | §6.3 重写 compose,改用 `${HOME}`/environment 插值 + 删 depends_on |
| U9 | P2 | codex | web 容器挂载整份 prod.env 含 AWS/LLM 凭证 | §6.3 改 `environment:` allowlist,只传 DATABASE_URL |
| U10 | P2 | architect | `pyproject.toml` [web] extra 未定义 | §6.5 新增完整 extra 定义 |
| U12 | P3 | architect | `data-flow.md` 仍用 `app.*` | §7.3 顺便修 |
| U14 | P3 | architect | SPA catch-all 吞 API 404 | §6.2 明示挂载顺序 |
| U11 | N/A | architect | spec port 5434 是旧值 | **证伪**:CLAUDE.md 已改为 5434,spec 对 |
| U13 | P3 | architect | login session cleanup concurrent DELETE | **不改**:3 人规模无害 |

v1 → v2 合计修订 17 处。全部修订后进 Round 2 fresh review 验证。

## 附录 B — 目录树预览

```
word_forge/
├── src/wordforge/
│   ├── web/
│   │   ├── __init__.py
│   │   ├── app.py                 # FastAPI factory
│   │   ├── deps.py                # engine / current_editor 依赖注入
│   │   ├── auth.py                # password hash + token 校验
│   │   ├── security.py            # cursor HMAC + 其它
│   │   ├── errors.py              # 全局 exception handler + envelope
│   │   ├── routes/
│   │   │   ├── auth.py
│   │   │   ├── words.py
│   │   │   └── audit.py
│   │   ├── schemas/
│   │   │   ├── auth.py
│   │   │   ├── words.py
│   │   │   └── audit.py
│   │   └── services/
│   │       ├── word_service.py    # PATCH/新建,复用 reviewer.patch
│   │       ├── audit_service.py
│   │       └── editor_service.py  # CLI 用
│   ├── cli.py                     # 加 `web` / `editors` 子命令
│   └── db/migrations/versions/
│       └── 0011_add_editor_workflow.py
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── api/                   # fetch 封装
│   │   ├── pages/                 # Login / Search / WordDetail / Audit
│   │   ├── components/            # 受控表单 / diff 预览 / 分页
│   │   └── main.tsx
│   └── dist/                      # build 产物,gitignore
├── tests/web/
│   ├── conftest.py                # make_editor / make_word helper
│   ├── unit/
│   └── integration/
├── Dockerfile.web
└── docker-compose.yml             # 加 wordforge-web service
```




