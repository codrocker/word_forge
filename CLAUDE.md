# wordforge — Claude 指北

git 库：`git@github.com:codrocker/word_forge.git`

## 跨项目先读

本仓与 `words_core`、`sailing_words` 共享的事实（schema / 数据流 / 飞书 wiki）集中在：

- [`../../docs/shared/data-flow.md`](../../docs/shared/data-flow.md) — `app.*` schema 约束，哪些表有 `updated_at`，`type`/`pos`/`word_id` 编码
- [`../../docs/shared/cross-repo-map.md`](../../docs/shared/cross-repo-map.md) — 本仓在 monorepo 中的位置
- [`../../docs/shared/feishu-wiki-index.md`](../../docs/shared/feishu-wiki-index.md) — schema 事实源（飞书 wiki）入口

**本仓改到跨项目可见的 schema / 数据流时，必须同步更新 `../../docs/shared/data-flow.md`。** 本仓内部的 pipeline 细节、LLM 坑、DB 运维规矩（下方"硬规矩"）留在本文件，**不外泄**给其它仓。

**Schema 设计硬规矩**:凡是要新建或修改数据表、讨论字段语义、写 DDL(不限 PG/MySQL/SQLite),**必须先 `lark-doc +fetch` 对应 wiki 页**,具体入口见 `../../docs/shared/feishu-wiki-index.md`。wiki 是组织的 schema 约定事实源,实例里 `SHOW CREATE TABLE` / `pg_dump` 只是当前状态的降级兜底。信任链 `代码 > wiki > 实例 DDL` —— wiki 常常领先于实例(新增字段、warning 如"临时设置 NULL"),只看实例会让新 schema 偏离组织约定。

**长文档分批写硬规矩**:单次 `Write` / `Edit` 输出 **超过 ~150 行**会触发 socket timeout / "connection closed unexpectedly"。正确做法:先 `Write` 骨架(header + 前 1-2 节),之后用 `Edit` 追加式每次加 100-150 行。适用场景:design spec / implementation plan / 长 README / 大型测试文件。不要偷懒一次写完。

## 如何访问外部资源(凭证全在 `~/.wordforge/`,chmod 600,不进 git)

访问任何 prod / 外部资源前 **先看下表查对应 env 文件是否已经有**,再决定是否新建。
`~/.wordforge/` 是本机所有 wordforge 相关凭证的唯一事实源,**不要**在代码里硬编码凭证,
**不要**自建新文件,**不要**让用户"手工跑 root 命令"——检查已有账号权限通常就够用。

| 资源 | env 文件 | 变量 | 权限/用途 |
|---|---|---|---|
| PG prod wordforge RDS | `~/.wordforge/prod.env` | `DATABASE_URL` | 读写 `domain.*` / `serving.*` / `pipeline.*` |
| PG test 本地 docker | (用 `export DATABASE_URL=postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge_test`) | — | pytest 专用,conftest guard 拒绝任何非本地+含 test 的 URL |
| momo MySQL `word` 库 | `~/.wordforge/momo.env` | `MOMO_MYSQL_HOST / PORT / USER / PASSWORD / DB` | 账号 `user_service_1` 在 MySQL 实例上有 **`ALL PRIVILEGES ON *.* WITH GRANT OPTION`** —— 可以 `CREATE DATABASE` / `CREATE USER` / `GRANT`,不需要 root 密码。验证: `mysql ... -e "SHOW GRANTS FOR CURRENT_USER()"` |
| MySQL `word_forge` 库 写账号 | `~/.wordforge/mysql_writer.env` | `WORDFORGE_MYSQL_WRITER_DSN` | 账号 `wordforge_writer`, CRUD + DDL on `word_forge.*`,供 `scripts/replicate/mirror_to_mysql.py` |
| MySQL `word_forge` 库 读账号 | `~/.wordforge/mysql_reader.env` | `WORDFORGE_MYSQL_READER_DSN` | `wordforge_reader`,`SELECT` only,供对账 / gozero |
| OSS `sailing-words-package-words` | `~/.wordforge/oss.env` | `OSS_ENDPOINT / OSS_BUCKET / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET` | 读写词包 JSON |
| Bedrock / LLM 凭证 | `~/.wordforge/prod.env` 里(AWS_* / OPENAI_API_KEY 等) | 按 provider | 各 completer 读 |

密码本身不进这张表,只写 env 文件路径和变量名。新凭证 append 一个新 env 文件,不要改已有
文件的语义;脚本里 `source ~/.wordforge/xxx.env` 然后读 env var,不要用 `--as root / sudo mysql -u root`。
任何一条命令如果你想说"需要 root/DBA",先查 `user_service_1` 权限或 PG 的 `wordforge` 超级
用户——十有八九已经够用。

**跑脚本 / 测试的两条硬约定**:

- **pytest**:执行前必须 `export DATABASE_URL='postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge_test'`
  (本地 docker test db)。`tests/conftest.py` 的 guard 会拒绝任何非本地 + 非 test 名的 URL,
  忘设会被直接 `pytest.exit()`。
- **跑本仓脚本用 `-m` 模块模式**,不是 `python scripts/x/y.py`。因为 `scripts/` 下的模块互相
  import 需要把仓根加入 PYTHONPATH,只有 `-m` 才能做到。正确: `.venv/bin/python -m scripts.packaging.export_sailing_sqlite`;
  错误: `.venv/bin/python scripts/packaging/export_sailing_sqlite.py` (ModuleNotFoundError: scripts).

## 硬规矩

### DB 安全事故防御

- **Prod/dev 数据库在阿里云 RDS,test 数据库在本地 docker**(物理隔离)。prod/dev 是
  `rm-cn-*.rwlb.rds.aliyuncs.com:5432/wordforge`(阿里云 RDS PG 17 serverless,杭州);
  test 是 `localhost:5433/wordforge_test`(docker-compose 的 wordforge-pg)。`tests/conftest.py`
  的 guard(`_looks_like_test_db()`)要求 host ∈ {localhost,127.0.0.1} **且** db 名含 'test',
  否则 `pytest.exit()`。**旧事故**:没隔离时一次 `pytest` 把 prod 的 75k `app.words` +
  `stage_artifacts` 几秒内 DROP 光,恢复花了半小时。
- **备份不能只备 `external_call_cache`**。`app.*` 和 `pipeline.words/stage_artifacts` 也值钱
  (前者是产物唯一 checkpoint,后者保存 momo id 映射)。长跑前全量 `pg_dump wordforge` 一次。
- **结构性变更 DROP/DDL 走 alembic migration,不手写 SQL**。手写会脱离 alembic 版本轨迹,
  `downgrade base` 清不掉手加的列,下次 upgrade head 炸 UndefinedColumn。
- **UPDATE 跨两个连接 = TOCTOU**。读 id 列表 + 改,必须同一个 `engine.begin()` 里。
- **写入前校验 `old_value`**。如果 prompt 问 LLM 要 `old_value`,代码必须对比 DB 当前值,
  不匹配就 raise。不校验的 old_value 字段比不存在还糟,给用户假的 drift 防护感。
- **不要并行跑长时运维脚本和 pytest**。即使 DB 隔离,两者仍共享 LLM quota / 代理 / 本地端口池。
- **项目不允许吞异常**。`except Exception` / `except (X, Exception)` = 裸 except,禁。
  唯一例外:runner 的 "double-fault guard"(worker 本体已抛完开始写 dead_letter 时)。

### LLM / 外部 API 调用

- **所有外部 API 调用必须走 `CacheStore`**。直接 `boto3.client(...).converse(...)` 是 bug——
  同 prompt 跑第二遍还花钱 = 设计缺陷。
- **缓存 key 必须加 script-specific discriminator**(如 `input_payload={"script": "xxx", "checker": name}`),
  否则两个脚本的相似 prompt 会相互污染。
- **新加 LLM provider 必须同步更新 pytest env-pop list**。`tests/test_cli.py` 的
  `_LLM_PROVIDER_ENV_KEYS` 枚举所有 provider env 凭证,漏一个会让"无 LLM 路径"测试被环境泄漏破坏。
- **Gemini 2.5 Pro 强制 thinking mode**。API reject `thinking_budget=0`(Flash 可 0)。
  Pro 的 thinking tokens 算 output 价($5/M),便宜任务用 Flash;必须 Pro 时 `max_tokens`
  要预留 ~2048 给 thinking,否则 finish_reason=MAX_TOKENS + 空文本。
- **google-genai SDK 选 AI Studio 还是 Vertex 是 env-implicit**:`GOOGLE_CLOUD_PROJECT` 有值时
  自动走 Vertex(要 ADC/SA key);只想用 `api_key` 必须显式 `vertexai=False`,否则报
  "Unknown name thinkingConfig at generation_config"(Vertex 字段名不同)。`api_version="v1"`
  的 AI Studio endpoint 不支持 thinking_config,默认 v1beta 才支持——没特殊原因别设 api_version。

### asyncio / 线程池 / 长跑进程

- **长时运维脚本必须有 heartbeat + stall watchdog**(判死标准:≥180s 没 future 完成)。
  watchdog 用 `os._exit(code)` 不是 `raise`——后者在 `with ThreadPoolExecutor` 内会被
  `__exit__` 的 join 卡到所有 worker socket 超时(~10 分钟),表象假的"exit 0 无报错"。
- **asyncio 场景别嵌套 ThreadPoolExecutor**。外层 10 × 内层 5 = 60 线程抢 GIL,主循环
  被饿死,heartbeat 发不出,8 小时完成 0 个词。正解:`asyncio.Queue` + N 个 worker 协程 +
  `asyncio.Semaphore` 限流;阻塞 IO 走 `asyncio.to_thread`;显式
  `loop.set_default_executor(ThreadPoolExecutor(max_workers=...))` 控制规模。

## Bedrock 从中国大陆调用

- **必走代理 + 已内置 Config**。Bedrock 封中国 IP;默认 timeout 对"半死"代理无效。
  `wordforge.llm.bedrock_completer.make_bedrock_completer` 已配好 `connect_timeout=10, read_timeout=60, retries=max_attempts=2`——不要在调用方 reimplement。
- **content_filter 不是 error**。Bedrock `stopReason=content_filtered` / OpenAI / Gemini
  `finish_reason=content_filter` 都是拒答不是系统错。completer 软降级:返回空 text + cost=0
  让 caller 跑完整批,不要 raise 崩整 run。
- **heartbeat 无缓冲输出**。`python -u` + `>> log 2>&1`,**不要**套 `tee ... | tail -N`——
  中间 pipe 的 block buffer 会吞 heartbeat(~4KB 才 flush),看起来像死了。

## 运维脚本 (scripts/*.py) 写法守则

1. **入参**:`--output` 默认 append 模式,配 `--skip-done-from`(支持多次传)读 jsonl union
   到 skip 集合,resume 零重复。
2. **LLM 调用**走 `LLMClient(store=CacheStore(engine), completers={...})`;定制 timeout/retry
   时自己写一个 completer 注入,不要直接 boto3。
3. **日志**:进度到 stdout,重大错误到 stderr,两者都要 `flush=True`。

## 数据模型地雷

- `app.meanings` / `app.mnemonics` / `app.sentences` **没有** `updated_at` 列,只有 `app.words` 有。
  `UPDATE ... SET x=:v, updated_at=now()` 这种抄过来就 UndefinedColumn。
- `app.words.word_id` 由 BIGSERIAL 分配,sequence 从 100001 起(migration 0005)。
  不再存外部系统的 word_id —— 需要"跟 momo 顺序大致对齐"时,让 ingest 输入按
  momo `word_id` 排好序(MySQL dump 加 `ORDER BY word_id`),BIGSERIAL 按插入
  顺序递增即可,并发下会有轻微乱序但足够。
- `type` 字段:1=单词,2=phrase。`pos` 字段:1-10 + 201=phrasal verb。两个独立。

## 常用诊断命令

```bash
# 缓存状态
docker exec wordforge-pg psql -U wordforge -d wordforge -c \
  "SELECT kind, COUNT(*) FROM pipeline.external_call_cache GROUP BY kind ORDER BY 2 DESC LIMIT 10;"

# 代理健康
curl -s --max-time 8 --proxy socks5h://127.0.0.1:1082 \
  https://bedrock-runtime.us-east-1.amazonaws.com/ -o /dev/null -w "%{http_code} %{time_total}\n"

# 卡住的长进程看它挂在哪
lsof -p <pid> | grep -E "TCP|IPv" | head
ps -M <pid>   # 各线程 CPU;全 0.0 = 全卡 I/O
```

## Git 守则

- `main` 用于 PR;按功能开 `feat/*`、修复开 `fix/*` 等语义化分支,不要直接在长期集成分支上堆提交。
- 只 commit 要求提交时才 commit;never push 没被要求就 push。
- pre-commit hook 失败不用 `--no-verify`;修根因再新建 commit。
