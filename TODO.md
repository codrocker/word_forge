# wordforge TODO

> 优先级由"事故驱动"决定,不是"checklist padding"。
> 本版经过 Gemini CLI + Codex 两次外部 review 裁剪。
> 每条完成打 `[x]`。

## P0 — 物理隔离 + 防呆(事故直接根因)

架构已定:**prod/dev = 阿里云 RDS(杭州),test = 本地 docker**。不需要
docker-compose.test.yml(原方案作废);本地 docker 就是 test 专用。

- [x] `conftest.py` session-level fail-closed guard(requires localhost + db 名含 test)
- [x] `.env.example` 分段说明 prod/test 两套 DATABASE_URL 用法
- [x] CLAUDE.md 硬规矩更新(阿里云 RDS host / 本地 docker test DB)

- [ ] **把当前本地 docker 数据迁移到阿里云 RDS**(recovery 跑完再做)
  - `pg_dump -Fc wordforge > wordforge.dump` → upload → `pg_restore` on RDS
  - 改 `~/.wordforge/prod.env` 的 DATABASE_URL 指向 RDS
  - 验证:阿里云 RDS 上 `app.words` count = 本地 docker count
  - 最后:本地 docker 的 `wordforge` database 改名或 drop,只留 `wordforge_test`
  - DoD:`source ~/.wordforge/prod.env && wordforge run --word apple` 走阿里云,跑通

- [ ] **创建本地 test database**
  - `docker exec wordforge-pg createdb -U wordforge wordforge_test`
  - 放到一个 `scripts/bootstrap_test_db.sh`,新开发者跑一次
  - DoD:新克隆 repo 的人跑 `bootstrap_test_db.sh && uv run pytest` 能过

- [ ] **Postgres DML-only role(最彻底防御)**
  - 在阿里云 RDS 上建 `wordforge_app` role,只有 `SELECT/INSERT/UPDATE/DELETE`
  - Alembic migration 用 `sailing_rds`(superuser)
  - 应用 `~/.wordforge/prod.env` 里用 `wordforge_app`
  - DoD:用 `wordforge_app` 连 `DROP SCHEMA CASCADE` 返回 permission denied

- [ ] **Alembic env.py 加生产降级防呆**
  - `run_migrations_online` 里判断:如果连接 host 不是 localhost,且命令是 `downgrade`,
    要求 `WORDFORGE_CONFIRM_PROD_DOWNGRADE=yes` 才放行
  - 光防 pytest 不够 — `uv run alembic downgrade base` 也得物理防
  - DoD:`DATABASE_URL=<阿里云 URL> alembic downgrade base` 拒绝执行

- [ ] **高危 DB 操作双人复核 checklist**
  - `DROP / TRUNCATE / ALTER TABLE / downgrade` 写到 CLAUDE.md,要求 agent(我)
    先输出"打算做 X,影响 Y 表 Z 条"并等待 y/N
  - 已有类似条目,补一条专门针对 DB 操作的模板
  - DoD:下次 agent 执行前必 echo 影响评估 + 等确认

## P0 — 代理韧性(四方 review 的头号发现)

- [x] `bedrock_completer.py` 加 `Config(connect_timeout=10, read_timeout=60, retries=2)`
- [x] `content_filtered` 降级为空 response(不 raise,不 crash worker)
- [ ] **主 pipeline stall watchdog**
  - 参考 `scripts/review_and_fix.py` heartbeat + 180s stall → `os._exit(2)` 模式
  - 下沉到 `src/wordforge/pipeline/runner.py` 或新建 `src/wordforge/ops/watchdog.py`
  - DoD:主 `wordforge run` 代理死了 <200s 内崩,不再无限挂

## P0 — 备份 + 恢复

- [ ] **docker volume 级原子快照**(几秒完成,防几秒清空)
  - 选型:`docker run --rm -v wordforge_wordforge_pg_data:/data -v /tmp/wordforge_smoke/volume_backup:/bak alpine tar -czf /bak/$(date +%F_%H%M).tgz /data`
  - 或用 ZFS/btrfs snapshot(如果 host 支持)
  - 比 pg_dump 快 10-100 倍,且**与 PG 在线状态无关**
  - 保留最近 24 个,每小时一次(cron/launchd)
  - DoD:snapshot 可在干净 volume 上还原,app.words 行数一致

- [ ] **`scripts/recover_from_momo.py`**(事故后 muscle memory 脚本化)
  - 输入:momo MySQL 连接,cache backup `.sql` 路径
  - 步骤:drop+recreate(如需)→ alembic upgrade → restore cache → ingest 121k → run pipeline
  - DoD:一条命令从空 DB 恢复到 75k+ `app.words`,失败自动退出带明确日志

- [ ] **`docs/HOW_TO_RUN.md` 补 "Recovery playbook" 章节**
  - 基于本次事故完整记录:momo 查询 SQL、build_ingest_inputs.py 逻辑、中间检查点
  - DoD:新人 / 未来的我读着能一步步恢复

## P1 — scripts/ 收编 + review 脚本拆分

- [ ] **`wordforge review` CLI 子命令**(两方 review 的 top-2 改动)
  - 拆分 900+ 行 `review_and_fix.py`:
    - `src/wordforge/reviewer/prompts.py`(5 checker + 1 fixer 模板)
    - `src/wordforge/reviewer/patch.py`(`_apply_patch` / `PatchDriftError` / `_check_drift`)
    - `src/wordforge/reviewer/worker.py`(`run_one_word` / `_run_checker`)
    - `src/wordforge/reviewer/runner.py`(asyncio Queue orchestrator,可复用)
    - `src/wordforge/reviewer/config.py`(`ReviewConfig` dataclass)
  - `scripts/review_and_fix.py` 变成 ≤ 10 行 thin wrapper 或直接删
  - DoD:`wordforge review --batch ... --apply` 工作;现有 jsonl 格式不变

- [ ] **`[review]` TOML 配置节**(替代 ReviewConfig 硬编码)
  - `configs/default.toml` 新增 `[review]` 节:haiku model、opus model、max_tokens、timings
  - `wordforge.config` 加对应 dataclass,可被 ReviewConfig 取代
  - DoD:改 review 使用的模型只改 toml,不改 py

- [ ] **`backfill_sentences.py` 处理**
  - 或走 LLMClient/CacheStore 或直接删
  - 当前 sentences 已由 examples stage 产出,此脚本是历史遗留
  - DoD:要么合规要么删,绝不留"下次跑烧钱"的地雷

## P1 — 主 pipeline 可观测性(Gemini 强烈建议升级)

- [ ] **`wordforge run` 加进度日志**(现在 log 空 8 小时是灾难性 UX)
  - 每 50 词 或 stage 切换时打一次 progress + ETA
  - 复用 P1 CLI 拆分后的 `_progress_stats` helper
  - DoD:tail -f 日志能看到每分钟至少 1 行实时进度

## P2 — 逻辑 bug(已知,数据风险低)

- [ ] **`_apply_patches_for_word` 的 meaning_ids 不随 delete 刷新**
  - 场景:同一个 word 先 `delete meanings[2]` 再 `update meanings[3].cn_paraphrase`,
    第二个 patch 的 index 对应的 meaning_id 已漂移
  - 修法:delete 后 refetch;或按 op 分两轮(先全 update,再全 delete)
  - DoD:单测覆盖这个顺序

- [ ] **模型常量从 `ReviewConfig` / `backfill_sentences` / `quality_review` 合并到 toml**
  - 同一个 model string 现在散在 3-5 处
  - 跟 P1 "[review] TOML" 合并做
  - DoD:`grep "us.anthropic.claude" src/ scripts/` 只在 toml 里出现

## P3 — nice-to-have(不做也不塌)

- [ ] `wordforge status` 子命令(`SELECT stage_name, COUNT(*)` 一行 SQL)
- [ ] `pipeline.stage_runs.error` 列加索引(扫全表在 121k 规模还能接受)
- [ ] `fingerprint.py` 文档补一句 "silent provider-side model rotation 不可检测"

---

## 已删(checklist padding,两方 review 共识)

- ~~`export.py` / `paraphrase.py` / `cli.py` 长文件拆分~~ — 300-500 行 just right,硬拆会导致 SQL 一致性漂移(export 是唯一写 app.* 的 stage)或跳转成本增加
- ~~`ruff.toml` 强制 max-lines~~ — 对一次性 pipeline 项目是 yak-shaving
- ~~`--rerun-drift-from` 支持~~ — drift 率 1-2%,全量重跑比专门做便宜
- ~~统一全局 Bedrock quota Semaphore~~ — 至今没撞过 quota 上限,YAGNI
