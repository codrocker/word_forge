"""login / logout / me — cookie-based session, slowapi rate limit on login."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.engine import Engine

from wordforge.web.auth import get_dummy_hash, verify_password
from wordforge.web.deps import current_editor, get_engine
from wordforge.web.errors import envelope_ok
from wordforge.web.schemas.auth import EditorOut, LoginRequest
from wordforge.web.security import (
    COOKIE_NAME,
    SESSION_TTL,
    cleanup_expired,
    cookie_secure,
    create_session,
    revoke_session,
)

router = APIRouter(prefix="/api/v1/auth")
limiter = Limiter(key_func=get_remote_address)


@router.post("/login")
@limiter.limit("10/60seconds")
def login(
    request: Request,
    body: LoginRequest,
    response: Response,
    engine: Engine = Depends(get_engine),
):
    # Best-effort cleanup outside login txn to avoid lock contention.
    with engine.begin() as cleanup_conn:
        cleanup_expired(cleanup_conn)

    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT id, email, display_name, password_hash, is_active "
                "FROM meta.editors WHERE email = :e"
            ),
            {"e": body.email},
        ).first()

        # Always run argon2 verify regardless of whether account exists,
        # eliminating timing oracle that leaks email existence.
        stored_hash = row.password_hash if row is not None else get_dummy_hash()
        password_ok = verify_password(stored_hash, body.password)

        if row is None or not row.is_active or not password_ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid email or password",
            )
        raw = create_session(conn, row.id)
    response.set_cookie(
        key=COOKIE_NAME,
        value=raw,
        httponly=True,
        samesite="strict",
        secure=cookie_secure(),
        max_age=int(SESSION_TTL.total_seconds()),
        path="/api",
    )
    return envelope_ok(
        {"editor": EditorOut(id=row.id, email=row.email, display_name=row.display_name).model_dump()}
    )


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    engine: Engine = Depends(get_engine),
):
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        with engine.begin() as conn:
            revoke_session(conn, raw)
    response.delete_cookie(COOKIE_NAME, path="/api")
    return envelope_ok(None)


@router.get("/me")
def me(editor: dict = Depends(current_editor)):
    return envelope_ok(EditorOut(**editor).model_dump())
