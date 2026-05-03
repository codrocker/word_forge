# wordforge TODO

未完成项。完成的内容不在这里,在 git log / CLAUDE.md / LESSONS_LEARNED.md。

## P0 — 数据安全剩余项

- [ ] **把本地 docker 数据迁移到阿里云 RDS**
  - `pg_dump -Fc wordforge > wordforge.dump` → upload → `pg_restore`
  - 改 `~/.wordforge/prod.env` 的 `DATABASE_URL` 指向 RDS
  - 迁完本地 `wordforge` DB 改名或 drop,只留 `wordforge_test`
  - DoD: `source ~/.wordforge/prod.env && wordforge run --word apple` 走阿里云跑通

- [ ] **Postgres DML-only role**(最彻底的 pytest 防呆)
  - 阿里云 RDS 上建 `wordforge_app`,只 `SELECT/INSERT/UPDATE/DELETE`
  - Alembic migration 用 superuser,应用用 `wordforge_app`
  - DoD: 用 `wordforge_app` 连 `DROP SCHEMA CASCADE` 返回 permission denied

## P0 — 代理韧性

- [ ] **主 pipeline stall watchdog**
  - 仿 `src/wordforge/reviewer/runner.py` 的 heartbeat + 180s stall → `os._exit(2)`
  - 下沉到 `src/wordforge/pipeline/runner.py` 或新建 `src/wordforge/ops/watchdog.py`
  - DoD: 主 `wordforge run` 代理死了 <200s 内崩,不再无限挂

## P0 — 备份 + 恢复

- [ ] **docker volume 级原子快照**(事故时的救命稻草,比 pg_dump 快 10-100x)
  - `docker run --rm -v wordforge_wordforge_pg_data:/data -v $BAK:/bak alpine tar -czf /bak/$(date +%F_%H%M).tgz /data`
  - 保留最近 24 个,每小时一次(launchd)
  - DoD: snapshot 可还原到干净 volume, `app.words` count 一致

## P1 — 可观测性

- [ ] **`wordforge run` 进度日志**
  - 每 50 词 / stage 切换打一次 progress + ETA
  - DoD: `tail -f log` 每分钟至少 1 行实时进度

- [ ] **`[review]` TOML 配置节**
  - `src/wordforge/configs/default.toml` 新增 `[review]` 节取代 `ReviewConfig` 硬编码 model
  - DoD: 改 review 的 model 只动 toml

## P2 — 已知逻辑 bug

- [ ] **`_apply_patches_for_word` 的 meaning_ids 不随 delete 刷新**
  - 同一 word 先 `delete meanings[2]` 再 `update meanings[3].cn_paraphrase`,
    第二个 patch 的 index 对应的 meaning_id 已漂移
  - 修法: delete 后 refetch;或按 op 分两轮
  - DoD: 单测覆盖该顺序

## P3 — nice-to-have

- [ ] `wordforge status` 子命令(一行 SQL `SELECT stage_name, COUNT(*)`)
- [ ] `pipeline.stage_runs.error` 列加索引
