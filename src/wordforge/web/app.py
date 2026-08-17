"""FastAPI app factory."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from wordforge.web.errors import envelope_err, register_exception_handlers
from wordforge.web.middleware import RequestIDMiddleware
from wordforge.web.routes.audit import router as audit_router
from wordforge.web.routes.auth import limiter, router as auth_router
from wordforge.web.routes.config_center import router as config_center_router
from wordforge.web.routes.experiments import router as experiments_router
from wordforge.web.routes.health import router as health_router
from wordforge.web.routes.words import router as words_router


def create_app() -> FastAPI:
    app = FastAPI(title="wordforge web admin", version="0.1.0")
    # slowapi first: state.limiter required by @limiter.limit decorators
    app.state.limiter = limiter

    def _rate_limit_exceeded_handler(request, exc):
        return JSONResponse(
            status_code=429,
            content=envelope_err("rate_limited", "too many requests"),
        )

    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(words_router)
    app.include_router(audit_router)
    app.include_router(experiments_router)
    app.include_router(config_center_router)
    # Static SPA — must be last so API routes take precedence
    dist_dir = Path(__file__).resolve().parent.parent.parent.parent / "frontend" / "dist"
    if dist_dir.is_dir() and (dist_dir / "index.html").exists():
        # Serve /assets/* (Vite build artifacts)
        assets_dir = dist_dir / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # SPA catch-all: any non-API path returns index.html for client-side routing
        _index_html = (dist_dir / "index.html").read_text()

        @app.get("/{full_path:path}", include_in_schema=False)
        async def _spa_catchall(request: Request, full_path: str) -> HTMLResponse:
            # Let API paths fall through to the 404 handler
            if full_path.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content=envelope_err("not_found", f"/{full_path} not found"),
                )
            return HTMLResponse(_index_html)
    else:
        logging.getLogger(__name__).warning(
            "frontend/dist not found or missing index.html; SPA static mount skipped"
        )
    return app
