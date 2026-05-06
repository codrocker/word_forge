"""Words read APIs: auth gate + basic search + 404."""
from fastapi.testclient import TestClient
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.app import create_app
from wordforge.web.services.editor_service import create_editor


def _login_client(email: str, pw: str = "pw1234ok") -> TestClient:
    create_editor(make_engine(), email, "RT", pw)
    c = TestClient(create_app())
    r = c.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return c


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


def test_search_requires_auth():
    c = TestClient(create_app())
    r = c.get("/api/v1/words")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthenticated"


def test_search_returns_envelope_items():
    email = "test-words-read@wordforge.dev"
    client = _login_client(email)
    try:
        r = client.get("/api/v1/words?limit=5")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"]["items"], list)
        assert "next_cursor" in body["data"]
    finally:
        _cleanup(email)


def test_detail_404():
    email = "test-words-404@wordforge.dev"
    client = _login_client(email)
    try:
        r = client.get("/api/v1/words/999999999")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
    finally:
        _cleanup(email)


def test_search_invalid_cursor_returns_400():
    email = "test-words-bad-cursor@wordforge.dev"
    client = _login_client(email)
    try:
        r = client.get("/api/v1/words?cursor=not-a-real-cursor")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_input"
    finally:
        _cleanup(email)


def test_search_limit_upper_bound():
    email = "test-words-limit@wordforge.dev"
    client = _login_client(email)
    try:
        r = client.get("/api/v1/words?limit=500")
        assert r.status_code == 400
    finally:
        _cleanup(email)
