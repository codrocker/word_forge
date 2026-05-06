"""Global exception handler + envelope.

All responses use `{ok, data, error}`. Never leak stack traces.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def envelope_ok(data: Any) -> dict:
    return {"ok": True, "data": data, "error": None}


def envelope_err(code: str, message: str, details: dict | None = None) -> dict:
    return {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message, "details": details or {}},
    }


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content=envelope_err("invalid_input", "validation failed", {"errors": exc.errors()}),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity(request: Request, exc: IntegrityError):
        return JSONResponse(status_code=409, content=envelope_err("conflict", "integrity violation"))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code_map = {
            401: "unauthenticated",
            403: "forbidden",
            404: "not_found",
            429: "rate_limited",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope_err(code_map.get(exc.status_code, "http_error"), exc.detail or ""),
        )

    @app.exception_handler(Exception)
    async def _uncaught(request: Request, exc: Exception):
        req_id = getattr(request.state, "request_id", "unknown")
        logger.exception("unhandled exception (request_id=%s)", req_id)
        return JSONResponse(
            status_code=500,
            content=envelope_err(
                "internal",
                "系统错误,已记录,请稍后重试",
                {"request_id": req_id},
            ),
        )
