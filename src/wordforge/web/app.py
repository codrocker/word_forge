"""FastAPI app factory."""
from __future__ import annotations

from fastapi import FastAPI

from wordforge.web.errors import register_exception_handlers
from wordforge.web.middleware import RequestIDMiddleware
from wordforge.web.routes.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="wordforge web admin", version="0.1.0")
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    # Static SPA mount added in M7
    return app
