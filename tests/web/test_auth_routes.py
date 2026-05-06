"""Auth routes: login / logout / me + drift + rate limit."""
from fastapi.testclient import TestClient
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.app import create_app
from wordforge.web.services.editor_service import create_editor


def _cleanup(email: str) -> None:
    e = make_engine()
    with e.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM meta.editor_sessions WHERE editor_id IN "
                "(SELECT id FROM meta.editors WHERE email = :e)"
            ),
            {"e": email},
        )
        conn.execute(text("DELETE FROM meta.editors WHERE email = :e"), {"e": email})
    e.dispose()


def test_login_me_logout_cycle():
    email = "test-auth-cycle@wordforge.dev"
    create_editor(make_engine(), email, "AC", "pw1234ok")
    client = TestClient(create_app())
    try:
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "pw1234ok"})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert "session" in r.cookies
        m = client.get("/api/v1/auth/me")
        assert m.status_code == 200
        assert m.json()["data"]["email"] == email
        client.post("/api/v1/auth/logout")
        m2 = client.get("/api/v1/auth/me")
        assert m2.status_code == 401
        assert m2.json()["error"]["code"] == "unauthenticated"
    finally:
        _cleanup(email)


def test_wrong_password_401():
    email = "test-auth-wrong@wordforge.dev"
    create_editor(make_engine(), email, "WP", "correctpw")
    client = TestClient(create_app())
    try:
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "unauthenticated"
        assert "session" not in r.cookies
    finally:
        _cleanup(email)


def test_me_without_cookie_401():
    client = TestClient(create_app())
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


def test_inactive_editor_cannot_login():
    email = "test-auth-inactive@wordforge.dev"
    create_editor(make_engine(), email, "IN", "pw")
    from wordforge.web.services.editor_service import deactivate_editor

    deactivate_editor(make_engine(), email)
    client = TestClient(create_app())
    try:
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "pw"})
        assert r.status_code == 401
    finally:
        _cleanup(email)


def test_rate_limit_after_10_attempts():
    """11th login attempt in 60s should return 429."""
    client = TestClient(create_app())
    for _ in range(10):
        client.post(
            "/api/v1/auth/login",
            json={"email": "nosuch@wordforge.dev", "password": "x"},
        )
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "nosuch@wordforge.dev", "password": "x"},
    )
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
