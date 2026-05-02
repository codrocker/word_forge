# wordforge — Claude 指北

gitp 库：
git@github.com:codrocker/word_forge.git
## 硬规矩

- **Prod/dev 数据库在阿里云 RDS,test 数据库在本地 docker**(物理隔离)。
  - prod/dev: `rm-cn-*.rwlb.rds.aliyuncs.com:5432/wordforge`(阿里云 RDS PG 17 serverless,杭州)
  - test:    `localhost:5433/wordforge_test`(docker-compose 的 wordforge-pg)
  - 凭证永远不进 git;`~/.wordforge/prod.env`(chmod 600)存 prod 的 `DATABASE_URL`,
    用前 `source ~/.wordforge/prod.env`。
  - `tests/conftest.py` 的 guard:host 必须是 localhost/127.0.0.1 **且** db 名必须
    含 'test',否则 `pytest.exit()`。逻辑见 `_looks_like_test_db()`。
  - **旧事故警示**:之前没有隔离时,一次 `pytest` 把生产 DB 的 75k `app.words` +
    所有 `stage_artifacts` 在几秒内 DROP 光,恢复要从 momo 源 + cache backup 花半小时重建。
- **备份只备 `external_call_cache`不够**。`app.*` 和 `pipeline.words/stage_artifacts`
  也值钱 — 前者是重建产物的唯一 checkpoint,后者保存了 ingest 的 momo id 映射。
  长跑前全量 `pg_dump wordforge` 一次。
- **项目不允许吞异常**。`except Exception` / `except (X, Exception)` 等价于裸 except,禁止。
  唯一例外是 runner 里的 "double-fault guard"(worker 本体已抛完开始记录 dead_letter 时)。
- **所有外部 API 调用必须走 `CacheStore`**。直接 `boto3.client(...).converse(...)` 是 bug。
  同一份 prompt 跑第二遍还花钱 = 设计缺陷。
- **不要并行跑长时运维脚本和 pytest**。即便一天你真的加了 test/prod 隔离,DB 以外
  还共享:同一个 LLM quota、同一个代理、同一个本地端口池。
- **结构性变更的 `DROP/DDL` 走 alembic migration,不手写 SQL**。手写会脱离 alembic
  的版本轨迹,`downgrade base` 清不掉你手加的列,下次 upgrade head 炸 UndefinedColumn。
- **写入前校验 `old_value`**。如果你的 prompt 向 LLM 要 `old_value`,代码必须对比 DB 当前值,
  不匹配就 raise。不校验的 old_value 字段比不存在还糟 —— 给用户假的 drift 防护感。
- **UPDATE 跨两个连接 = TOCTOU**。读 id 列表 + 根据 id 改,必须在同一个 `engine.begin()` 里。
- **长时运维脚本必须有 heartbeat + stall watchdog**。判死标准:≥180s 没有 future 完成。
  watchdog 必须用 `os._exit(code)`,不是 `raise` —— `raise` 在 `with ThreadPoolExecutor`
  内会被 `__exit__` 的 join 卡到所有 worker 的 socket 超时(~10 分钟),用户看到的是假的
  "exit 0 无报错"。
- **asyncio 场景别嵌套 ThreadPoolExecutor**。外层 10 thread × 内层 5 thread = 60 线程抢
  GIL,主循环 tick 被饿死,heartbeat 发不出,8 小时完成 0 个词。正解:`asyncio.Queue` +
  N 个 worker 协程 + `asyncio.Semaphore` 限流;所有阻塞 IO 走 `asyncio.to_thread`,
  显式设 `loop.set_default_executor(ThreadPoolExecutor(max_workers=...))` 控制规模。
- **LLM provider 添加新的必须同步更新 pytest 的 env-pop list**。`tests/test_cli.py` 里
  `_LLM_PROVIDER_ENV_KEYS` 枚举了所有 provider 的 env 凭证,漏一个就会让"无 LLM 路径"
  的测试被环境泄漏破坏。新增 completer 时一起改。
- **Gemini 2.5 Pro 强制 thinking mode,不能 disable**。API 直接 reject `thinking_budget=0`
  并返回 "This model only works in thinking mode"。Flash 可以设 0。Pro 的 thinking tokens
  算 output 价($5/M),所以便宜任务用 Flash;质量必须用 Pro 时记得 `max_tokens` 要预留
  thinking budget(~2048),否则 output 被 thinking 吃光 → finish_reason=MAX_TOKENS + 空文本。
- **google-genai SDK 选 AI Studio 还是 Vertex 是 env-implicit**:如果 `GOOGLE_CLOUD_PROJECT`
  有值,SDK 自动走 Vertex path(需要 ADC 或 SA key);只想用 `api_key` 必须显式传
  `vertexai=False`,否则会报 "Unknown name thinkingConfig at generation_config"(Vertex
  字段名不同)。
- **`api_version="v1"` 的 AI Studio endpoint 不支持 thinking_config**,默认 v1beta 才支持。
  没特殊原因别设 api_version,走 SDK 默认。

## Bedrock 从中国大陆调用

- **必须走代理**。Bedrock 对中国 IP 有区域封锁;macOS 系统代理(Lark/VPN 工具)或 SOCKS5 都行。
- **代理会死**。典型死法:TCP 连接保持 ESTABLISHED,但不再转发字节 → boto3 的
  `read_timeout` 不触发,进程永远卡住。
- **必须配 `Config(connect_timeout=10, read_timeout=60, retries=max_attempts=2)`**。默认 60s
  connect + 60s read 对"只是慢"的代理 OK,但对"半死"的代理没用。已内置到
  `wordforge.llm.bedrock_completer.make_bedrock_completer`,不要再在调用方 reimplement。
- **heartbeat 必须无缓冲输出**。`python -u` + `>> log 2>&1`,**不要套 `tee ... | tail -N`** ——
  中间那个 pipe 的 block buffer 会吞掉 heartbeat(~4KB 才 flush),看起来就像程序死了。
- **content_filter 不是 error**。Bedrock `stopReason=content_filtered` / OpenAI / Gemini
  `finish_reason=content_filter` 都是模型拒答,不是系统错。completer 要软降级:返回空 text
  + cost=0,让 caller 把整批跑完,而不是 raise 崩掉 worker(63k 批里一个拒答炸整个 run)。

## 运维脚本 (scripts/*.py) 写法守则

1. **入参**:`--output` 默认是 append 模式,配一个 `--skip-done-from`(支持多次传)
   读 jsonl 并 union 到 skip 集合。这样 resume 零重复。
2. **所有 LLM 调用**:走 `LLMClient(store=CacheStore(engine), completers={...})`。
   定制 timeout/retry 时自己写一个 completer 注入,不要直接 boto3。
3. **缓存 key 里必须加 script-specific discriminator**(例如 `input_payload={"script": "xxx",
   "checker": name}`),否则两个脚本的相似 prompt 会相互污染。
4. **ruff E501**:prompt 模板天然长,在脚本顶部加 `# ruff: noqa: E501` 豁免。
5. **日志**:进度日志到 stdout,重大错误到 stderr,两者都要 `flush=True`。

## 数据模型地雷

- `app.meanings` / `app.mnemonics` / `app.sentences` **没有** `updated_at` 列,只有 `app.words` 有。
  `UPDATE ... SET x=:v, updated_at=now()` 这种抄过来就 UndefinedColumn。
- `app.words.word_id` 由 BIGSERIAL 分配,sequence 从 100001 起(migration 0005)。
  不再存外部系统的 word_id —— 需要"跟 momo 顺序大致对齐"时,让 ingest 按 momo
  word_id 排序的 word_list 输入,BIGSERIAL 自然按插入顺序递增,并发下会有轻微
  乱序但足够。`recover_from_momo.py` 的 MySQL dump 加了 `ORDER BY word_id`。
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
