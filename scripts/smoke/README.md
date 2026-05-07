# web_admin smoke test

最快验证 web admin backend 活着的一条脚本,纯 curl + jq,不依赖 pytest。

## 用法

```bash
# 启动后端(另一个终端)
uv run wordforge web --port 8000

# 跑 smoke(默认连 localhost:8000,用 dev@wordforge.local / devpass123)
bash scripts/smoke/web_admin_e2e.sh

# 改 base / 凭证
WORDFORGE_SMOKE_BASE_URL=http://127.0.0.1:8000 \
WORDFORGE_SMOKE_EMAIL=you@wordforge.local \
WORDFORGE_SMOKE_PASSWORD=xxx \
  bash scripts/smoke/web_admin_e2e.sh
```

退出码 0 = 10 步全过;非 0 = 某步失败,stderr 会打哪一步 + HTTP body + request_id。

## 覆盖
health / login / me / search / detail / audit / PATCH drift 409 / logout。不覆盖写入(要求现有 editor 账号,不建 test 数据)。
