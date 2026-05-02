# wordforge

背单词 App 底层词条数据生产 pipeline。

跨项目背景（本仓在 monorepo 中的位置、数据流、schema 约束）：见 [`../../docs/shared/`](../../docs/shared/README.md)。
本仓内部设计规约见 `CLAUDE.md`。

## 本地 Postgres 的定位

`docker-compose.yml` 起的 `postgres:15` 仅用于：

1. **migration 验证**：在本地反复跑 `alembic upgrade/downgrade` 校验幂等与可回滚
2. **单测沙盒**：pytest fixture 起干净库，测完 `docker compose down -v` 丢弃

**生产数据不在本地**——真实词库数据住在云服务器 PG，pipeline 通过 `.env` 中的 `DATABASE_URL` 切换目标：

- 本地开发/测试：`DATABASE_URL=postgresql+psycopg://wordforge:wordforge@localhost:5433/wordforge`
- 云上运行：指向云 PG 的内网地址

Pipeline 代码本身无状态，本地跑还是云上跑只取决于 `DATABASE_URL` 指向谁。schema 事实源在 Feishu Wiki，任何 schema 变更先在本地 Docker PG 验完 migration，再 apply 到云 PG。

**不要**往本地 Docker PG 灌真实词库数据，也不要当它是缓存——保持它一次性、可随时销毁。
