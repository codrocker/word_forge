# Web Admin -- Release Notes

## 概述

wordforge Web Admin 是内部词条审阅/编辑后台,供运营编辑在浏览器中搜索、查看、编辑词条并审阅变更历史。本次交付覆盖 M1-M7 全部里程碑,从 schema 扩展到前后端完整实现,已通过 110 项自动化测试。

## 实施范围

- **M1 -- 基础设施**：alembic migration (domain.words.status/quality_flag, meta.editors, meta.audit_log, serving.word_payload)；make_engine sync-only 架构；rebuild_word_payload 提取；pipeline export SET status=1；Dockerfile.web + compose service。
- **M2 -- 认证**：editors CLI (create/list/deactivate)；cookie session login/logout/me；slowapi rate-limit。
- **M3 -- 只读 API**：keyset cursor 分页；GET /words search + GET /words/{id} detail；GET /audit with filters。
- **M4 -- 写入 API**：audit_service.write_audit；apply_web_changes drift-aware all-or-nothing；PATCH /words/{id} + serving rebuild + drift 409；POST /status + /quality。
- **M5 -- 创建**：POST /words create + UNIQUE 409 fallback + source stamp。
- **M6 -- 前端**：Vite + React TS + TailwindCSS；Login / Search / WordDetail+Edit / Audit 四页面；ErrorBoundary + request_id 展示。
- **M7 -- 集成与文档**：FastAPI mount SPA dist (catch-all)；cross-repo docs 更新；本 release notes。

## 统计

| 指标 | 数值 |
|---|---|
| Backend tests (web suite) | 52 |
| Backend tests (全仓 pass) | 110+ |
| Frontend build | 0 errors, 0 warnings |
| Commits (feat/web-admin) | 79 |
| Backend 核心文件 | `src/wordforge/web/` 下 app / auth / cursor / deps / errors / middleware / security + routes/ + schemas/ + services/ |
| Frontend 页面 | `frontend/src/pages/` Login, Search, WordDetail, Audit, NotFound |
| Frontend API 层 | `frontend/src/api/` client, auth, words, wordDetail, audit, types |

## 部署 Cheatsheet

```bash
# 1. 前端 build
cd frontend && npm ci && npm run build

# 2. 环境准备
source ~/.wordforge/prod.env
export WORDFORGE_WEB_COOKIE_SECURE=true   # prod HTTPS

# 3. DB migration
uv run alembic upgrade head

# 4. 创建首个编辑者
uv run wordforge editors create --email ops@company.com --display-name "Ops"

# 5. 启动
docker compose up -d wordforge-web
# 或裸机:
uv run wordforge web
# → http://localhost:8000/
```

## 已知限制 (MVP 不做)

- 无 RBAC / 多角色权限,所有 editor 等权
- 无批量编辑 (bulk update) API
- 无实时通知 / WebSocket push
- 无 i18n (仅中文界面)
- 无 HTTPS 终止 (依赖前置 nginx / ALB)
- 无 audit log 导出 (CSV/Excel)
- 无自动化 E2E 测试 (Playwright / Cypress)
- 前端无 SSR,SEO 不适用 (内部工具)
