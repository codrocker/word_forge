# WordForge Admin Frontend

Vite + React 18 + TypeScript SPA for the WordForge web admin panel.

## Stack

- **Build**: Vite 6
- **UI**: React 18, Semi Design（组件库，样式随组件模块自动引入）+ Tailwind CSS（布局工具）
- **Routing**: React Router v6
- **HTTP**: Axios (withCredentials, envelope interceptor)
- **State/Cache**: TanStack Query v5
- **Forms**: React Hook Form + Zod（Semi Input 通过 `RhfInput` 适配接入）

## Development

```bash
npm install
npm run dev      # http://localhost:5173, proxies /api -> localhost:8000
```

## Production Build

```bash
npm run build    # outputs to dist/
```

`dist/` is served by the FastAPI backend via StaticFiles mount.
