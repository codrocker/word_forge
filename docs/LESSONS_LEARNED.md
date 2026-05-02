# wordforge 踩坑与经验

> 记录重大事件的**原因 + 补救 + 结构性修复**,供以后的我(或同事)避免重复。
> 简略的硬规矩清单在 `CLAUDE.md`;本文是背景说明,给需要深挖的人。

## 2026-04-30 ~ 05-01 大事故链

短短 36 小时连环出了 7 件事,每一件都是后续硬规矩的出处。按时间顺序记:

### 1. 嵌套 ThreadPoolExecutor 饿死 asyncio event loop(8 小时零进度)

**现象**:`scripts/review_and_fix.py` 启动后 8 小时 42 分 CPU 5%、log 345 字节、jsonl 0 行,
但 `external_call_cache` 持续写入(~400/min),`lsof` 20+ ESTABLISHED 到代理。

**诊断**:外层 `ThreadPoolExecutor(10 words)` 里嵌 `ThreadPoolExecutor(5 checkers)` = 60 个
worker 线程。haiku 调用在线程里 CPU-bound 解 JSON、算 cost、写 cache,60 线程抢 GIL。主
循环的 `fwait(pending, timeout=10)` 也是 CPU-bound(poll 63,320 个 future 状态),永远抢不到
时间片。cache 在涨是因为 boto3 IO 会 release GIL,但主线程回不来,heartbeat 永不出,
fut.result() 永不被收。

**修复**:整体重写成 `asyncio.Queue` + N worker 协程 + `asyncio.Semaphore(N*3)`
限流;所有阻塞 IO 走 `asyncio.to_thread`(释放 GIL 给 event loop);heartbeat / watchdog
是独立 asyncio task。

**新硬规矩**:asyncio 场景不嵌 ThreadPool,单层协程 + Semaphore。

---

### 2. "tee | tail -N" 吞掉 stdout,watchdog 假死

**现象**:watchdog 明明 `raise RuntimeError("pipeline stalled")`,但用户 `tail log` 看不到,
进程还挂 11 分钟才真正退出,exit code 0(骗人)。

**诊断**:`nohup uv run ... | tee log | tail -5` 中间那个 pipe 是 block-buffered(~4KB flush),
heartbeat 小输出被缓冲住;同时 `raise` 在 `with ThreadPoolExecutor:` 内会被 `__exit__` 的
join 阻塞,等所有 worker 线程 socket 超时(boto3 read_timeout=60s × 数十个)才 unwind,
traceback 最终被 pipe buffer 和 exit code 一起丢掉。

**修复**:
1. watchdog 改 `os._exit(2)` + `out.flush() + os.fsync()` 显式刷盘,绕过 __exit__。
2. 后台脚本**不再**用 `tee | tail`。直接 `>> log 2>&1`,`tail -f log` 实时可见。

**新硬规矩**:heartbeat 必须无缓冲;watchdog 用 `os._exit` 不是 `raise`。

---

### 3. pytest 并发跑 + 共享 DATABASE_URL → 生产库 DROP 光

**现象**:我为做 smoke + 测试顺手 `make test` 并行,10 秒内 `app.words` 从 75,138 → 0,
`alembic_version` 表 0 行,整个 pipeline schema 消失。

**诊断**:`tests/conftest.py:at_head` fixture 做 `alembic downgrade base`,会 DROP app 与
pipeline schema;wordforge 当时**没有 test DB 隔离**,pytest 的 `DATABASE_URL` 就是生产的。
对并发(并行 smoke + pytest)意味着"pytest 的开场每次都炸掉生产"。

**修复三层**:
1. `tests/conftest.py` 加 session-scope guard:host 必须 localhost/127.0.0.1 + db 名含 'test'
   + 硬黑名单拒 `wordforge` 名称 + 删后门 env。
2. `docker-compose.test.yml` + `make test` → 独立 `wordforge-pg-test` 容器(port 5434,
   DB `wordforge_test`),与生产 docker 同时运行但物理隔离。
3. `src/wordforge/db/migrations/env.py` 加 alembic downgrade guard:host 非 local 时拒绝
   `downgrade`,除非 `WORDFORGE_CONFIRM_PROD_DOWNGRADE=yes`。
4. 长期方向:prod/dev 迁阿里云 RDS(另一台机器,pytest 物理到不了)。

**恢复过程**:momo MySQL 导出 122k 单词 → `build_ingest_inputs.py` 生成 word_list + id_map →
`wordforge ingest --id-map` → `wordforge run --concurrency 30` 命中 cache 从 200k 条
`external_call_cache` 备份里快速重建。总 downtime ~45 分钟,花费 $1k(paraphrase 那波
多出的 47k 新词是真花钱)。

**新硬规矩**:test/prod 物理隔离、conftest 硬 guard、alembic env.py guard、不要并行跑
pytest 和长运维。整套放 CLAUDE.md 和 `TODO.md` P0。

---

### 4. pipeline.words.source_word_id 设计反复两次

**第一次**:同时给 `app.words` 和 `pipeline.words` 加 `source_word_id` 列。用户 kill 进程说
"我是不是说的直接替换 word_id?你在最终结果表里补这个 source 干嘛?"。

**第二次**:只给 `pipeline.words` 加(运输通道),`app.words.word_id` 直接用 momo 的 id
(INSERT 显式指定),没有 momo 映射的用 PG serial。

**教训**:**需求不清时先问一句 "这个字段是中间态还是终态?"**,不要猜两次。

结构性沉淀:`src/wordforge/stages/export.py:_upsert_app_words` 有两条 SQL 分支(has_wid / no_wid),`src/wordforge/db/migrations/versions/0004_add_source_word_id.py` 只动 pipeline schema。

---

### 5. paraphrase 花 $1,045 vs 预估 $200 的 10x 差距

**原因链**:
- 一开始误以为"全量 cache 命中,不花钱"——实际之前 pipeline 只跑过 75k 词,momo 有
  122k,**47k 是从未见过的新词**,paraphrase (opus-4-7 at $15 in/$75 out) 全价调用。
- 没做事前 cost 估算;没在启动时打印 "expected cache hit rate"。
- Recovery 跑起来 1 小时花了 $45,被问"是不是太贵了"才警觉。

**补救**:
- 中途 kill,derivatives/examples/mnemonic 三个下游 stage 切到 Gemini 2.5 Flash
  (~$0.0002/词 vs Bedrock $0.0086/词 = 45x 便宜),完成全量成本降到 ~$150。
- 新加 `src/wordforge/llm/pricing.py` 作为 25+ 模型价格单一来源,`compute_cost()` 调用。
- `configs/default.toml` 每个 stage 的 `cost_estimate_usd` 是部署前就该估算的字段,长跑
  前先乘以 N_words 算总价,超过 $100 先问。

**长期方向**:`wordforge plan --stage X` 不只看 cache hit 率,还要按 `cost_estimate_usd ×
needs_rerun` 打印预估总花销。

---

### 6. Gemini 2.5 Pro 强制 thinking mode,API 对 budget=0 报错

**诊断链**(花了 20 分钟):
1. mnemonic 用 gemini-2.5-pro,max_tokens=400,API 返回 MAX_TOKENS + 空文本 → 2048 思考
   token 吃光了 output 预算。
2. 尝试 `thinking_config={"thinking_budget": 0}` dict 形式 → API 报 "Unknown name
   thinkingConfig at 'generation_config'"。
3. 直接命令行 minimal repro 用 `types.ThinkingConfig(thinking_budget=0)` 对 flash 成功,
   对 pro 失败:"Budget 0 is invalid. This model only works in thinking mode." **Pro 不
   允许关 thinking**。
4. 同一 SDK 下 `api_version="v1"` 拒 thinking_config,默认(v1beta)接受。
5. 同一 SDK 下 `GOOGLE_CLOUD_PROJECT` 被设导致 SDK 自动切 Vertex mode,报 "generation_config"
   而不是 camelCase "generationConfig",进一步误导。

**补救(按 google-genai SDK 的坑):**
- 传 `types.ThinkingConfig(...)` 对象,不是 plain dict。
- 显式 `vertexai=False` 禁止 env-based 自动切。
- 不要设 `api_version`,用 SDK 默认(v1beta)。
- 2.5 Pro 场景:caller 传的 `max_tokens` 当作纯输出预算,completer 内部加上
  `thinking_budget` buffer(默认 2048),最终 `max_output_tokens = max_tokens + think_budget`。
- 如果是便宜/浅任务,用 Flash 并 `thinking_budget=0`;Pro 只在质量确实需要时用。

---

### 7. 新增 LLM provider 忘了同步测试的 env-pop 白名单

**现象**:加完 Gemini / OpenAI / Qwen / Azure 四个 completer 后 `make test` 炸:有 2 个
CLI 测试期望"无 LLM 路径"(fetch_dict + phonetic only),结果 shell 里的 GEMINI_API_KEY
让 registry 注册了 gemini completer,pipeline 跑了 LLM stage,断言失败。

**诊断**:`tests/test_cli.py` 原先 `env.pop` 只清 AWS + Anthropic 三个 key。新加 provider
后,env 清理没同步,在持有真实 key 的开发机上跑 pytest 会"穿透"测试意图。

**补救**:抽 module-level `_LLM_PROVIDER_ENV_KEYS` tuple 集中管理 env 名,新加 provider
时加一行 tuple 成员。

**新规矩**:`tests/test_cli.py` 的 `_LLM_PROVIDER_ENV_KEYS` 必须和 `src/wordforge/llm/*.py`
保持同步 — 搞 provider 时一起改。

---

## 元教训

**以下反复出现**,值得独立记住:

1. **ruff `--fix` 对 multi-line import 的重排会悄悄把 `apply_patch`、`check_drift` 这种
   "看起来没被用"的 import 删掉**(F401)。做 re-export shim 时必须显式加
   `# noqa: F401`,否则拆分过程中会丢符号。
2. **Python 代码删大块宁用 `python3 -c` 脚本切 list,不用 sed/Edit**:行号会失配,尤其
   带 CJK 字符时 byte-vs-char off-by-one 常见。
3. **每完成一步 commit + push 是便宜的**,不要攒 10 个 commit 一起 push;今晚就靠这个
   git reflog 回滚了一次失败的 Edit。
4. **subagent / LLM 的建议按论据强度裁决,不按票数**。今天 Gemini 和 Codex 有两处分歧
   (长文件要不要拆 / `_looks_like_test_db` 的 heuristic),按他们各自给的理由裁决比
   "多数票"更靠谱。
5. **"一次性运维脚本"的借口会膨胀到 1000 行**。`scripts/review_and_fix.py` 一度 1044 行,
   拆完 3 commit 变成 20 行 shim。要么一开始就进 `src/`,要么每次改动都考虑是不是该进。
