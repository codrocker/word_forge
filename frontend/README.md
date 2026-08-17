# WordForge Admin Frontend

Vite + React 18 + TypeScript SPA for the WordForge web admin panel.

## Stack

- **Build**: Vite 6
- **UI**: React 18, Tailwind CSS
- **Routing**: React Router v6
- **HTTP**: Axios (withCredentials, envelope interceptor)
- **State/Cache**: TanStack Query v5
- **Forms**: React Hook Form + Zod

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
