"""FastAPI app factory."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from wordforge.web.errors import envelope_err, register_exception_handlers
from wordforge.web.middleware import RequestIDMiddleware
from wordforge.web.routes.auth import limiter, router as auth_router
from wordforge.web.routes.health import router as health_router


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
    # Static SPA mount added in M7
    return app
