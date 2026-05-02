# wordforge — How to run

状态快照：branch `allen`, 211 tests passing, 50 词 live run on Bedrock Sonnet-4 全通 (**未 push**).

## 前置

```bash
# 1. Postgres
cd /Users/allen/code/ai_ark/wordforge
docker compose up -d   # wordforge-pg @ localhost:5433 (healthy)
export DATABASE_URL=postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge

# 2. Schema
uv run alembic upgrade head
# → 0001 schemas / 0002 app.* / 0003 pipeline.*

# 3. boto3 (Bedrock) — this machine's preferred provider
uv pip install boto3
# AWS_BEARER_TOKEN_BEDROCK 已在 shell env 里 (CLAUDE_CODE_USE_BEDROCK=1)
```

## 测试环境 / 生产环境的物理隔离

- **dev/prod**：阿里云 RDS (Hangzhou, PG 17)。连接 URL 放 `~/.wordforge/prod.env`
  (chmod 600)，用前 `source ~/.wordforge/prod.env`。生产凭证**不能进 git**。
- **test**：本地 docker 容器 `wordforge-pg-test` @ port **5434**，DB 名
  `wordforge_test`。`tests/conftest.py` 的 guard 会拒绝任何 host 不是 localhost
  或 DB 名不含 'test' 的 DATABASE_URL（硬黑名单还会拦截 `wordforge` 等生产名）。

## Running tests

```bash
# 一次性：启动测试容器 + 跑 alembic + 跑 pytest。coexist with the dev
# container on 5433, so you can keep `wordforge run` running while testing.
make test

# 细粒度：仅启动测试容器
make test-db-up

# 结束测试容器（volume 保留，下次秒起）
make test-db-down
```

`scripts/bootstrap_test_db.sh` 是 make 背后的脚本，可单独跑。
新增 LLM provider 后记得把它的 env key 加进 `tests/test_cli.py` 的
`_LLM_PROVIDER_ENV_KEYS`，否则 "skip LLM stages" 测试会因环境泄漏而失败。

## Smoke test — 1 词跑通全链路 (bedrock sonnet-4, real Youdao)

```bash
echo "apple" > /tmp/words.txt
uv run wordforge ingest /tmp/words.txt --batch SMOKE1
# → ingested: inserted=1 deduped=0 skipped_empty=0

# YoudaoClient 现在走 dict.youdao.com/jsonapi (真 endpoint, 免 key, 免 sleep).
# 无需设置 stub. 测试里用 WORDFORGE_STUB_YOUDAO_JSON='{"simple":...}'
uv run wordforge run --batch SMOKE1
# → run complete: batch=SMOKE1 | 1 words × 8 stages = 8 events |
#    ok_events=8 failed_events=0 skipped_events=0 pruned_events=0
```

## 50 词 live run (verified 2026-04-30)

```bash
cat > /tmp/words_50.txt <<'EOF'
# 50 words: fruits/verbs/adjectives/hard words/phrasal verbs (see git log for list)
EOF
uv run wordforge ingest /tmp/words_50.txt --batch BATCH_50_V1
time uv run wordforge run --batch BATCH_50_V1
# → ok_events=400, failed=0, pruned=0
# → wall time ~3 min (cache-cold Youdao + 200 LLM calls, Semaphore(5))
# → cost ~$1.02 (bedrock Sonnet-4, ≈ $0.02/word)
```

## 验证产出

```bash
uv run python -c "
from sqlalchemy import text
from wordforge.db.engine import make_engine
e = make_engine()
with e.begin() as c:
    for r in c.execute(text('SELECT word_id, form, phonetic_us, source FROM app.words LIMIT 5')):
        print(r)
    for r in c.execute(text('SELECT meaning_id, pos, cn_paraphrase FROM app.meanings LIMIT 5')):
        print(r)
    for r in c.execute(text('SELECT mnemonic_id, content FROM app.mnemonics LIMIT 5')):
        print(r)
"
```

预期 (50 词 live run 实测):
- `app.words`: form="apple", phonetic_us="ˈæp(ə)l", audio_us=`https://dict.youdao.com/dictvoice?audio=apple&type=2`, plural="apples", source=`pipeline:bedrock:us.anthropic.claude-sonnet-4-20250514-v1:0:paraphrase_v1`
- `app.meanings`: cn_paraphrase="苹果", pos=1 (多义词自动拆分 3-6 条)
- `app.mnemonics`: `{"kind":"phonetic","text":"阿婆拿着红苹果，笑着说：「阿婆了！」","sound_alike":"阿婆了"}`

"杠杆联想"风格示例 (来自真跑):
- apple → 「阿婆了」
- ambition → 「俺必胜」
- serendipity → 「色人滴屁体」（意外发现美女放屁）
- pick up → 「皮卡丘被训练师「皮卡！」一声捡起来」

## Dry-run 成本估算

```bash
uv run wordforge plan --stage paraphrase
# → plan: stage=paraphrase batch=<all> | total_candidates=N has_artifact=M
#    (fingerprint unchecked — P5 will verify) needs_rerun=N-M |
#    estimated_cost_usd=0.xxxx (source=config) | sample: word1, word2, ...
```

## DLQ 管理

```bash
uv run wordforge dlq list
# 看哪些词跑崩了

uv run wordforge dlq replay --word-id 42
# 重置 pipeline.words.status='new'; 下次 run 会重试
```

## 换 Anthropic direct API (非 bedrock) 的路径

```bash
unset AWS_BEARER_TOKEN_BEDROCK AWS_ACCESS_KEY_ID
export ANTHROPIC_API_KEY=sk-...
uv pip install anthropic
# 然后改 src/wordforge/configs/default.toml 里每个 LLM stage:
#   provider = "anthropic"
#   model = "claude-opus-4-20250514"  (or whatever Anthropic SDK accepts)
```

CLI 选 bedrock / anthropic 基于 env (cli.py:167-175): `_register_bedrock() or _register_anthropic()`. Bedrock 优先。

## 已知 TODO（非本次 autopilot scope）

1. **Youdao endpoint** — ✅ 已接入 `/jsonapi` (免 sleep, 免 key, 50 词 live 验证过)
2. **Prompt 质量** — ✅ v1 吸收了 select_word_project prompt3 的杠杆联想方法，50 词 live 质量良好；大规模跑后可再 iterate
3. **Mnemonic parser** — ✅ 容错 LLM 嵌套未转义双引号
4. **未 push 到 origin** — 等你 review 后手动 `git push origin allen`

## Review archive

每一步的 tri-review battle 归档在:
- `.omc/p4-tri-review/` — P4 plan 四轮 fresh review + battle
- `.omc/p5a-review/` / `.omc/p5b-review/` / `.omc/p5c-review/` — 单轮 architect review
- `.omc/final-review/` — 最终 gemini + code-reviewer 全量扫描 + bug fix

## Autopilot 交付 summary

```
branch allen: 9e9a552 → 37dc7f9 (22 commits)
- P4 plan/run CLI              : 63af1fe..ffd6a31 (5 commits)
- P5a fetch_dict + phonetic    : 0b119a9..776bdd4 (5 commits)
- P5b 4 LLM stages             : 1216ece..22ef5ce (6 commits)
- P5c quality_gate + export    : 15efeca..a9a4574 (3 commits)
- P7 dlq                       : 277820d..b09160d (4 commits)
- Final review fixes           : f0fe37c (1 commit)
- Bedrock completer            : 60595d9 (1 commit)
- Test env fix                 : 37dc7f9 (1 commit)

Total: 208 tests, ruff/mypy clean, live smoke on bedrock sonnet-4 (1 word, 8 stages, $0.0066, 11s)
```
