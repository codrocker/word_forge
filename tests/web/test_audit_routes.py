"""Audit log route tests."""
from fastapi.testclient import TestClient
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.app import create_app
from wordforge.web.services.editor_service import create_editor
from tests.web.conftest import TEST_PASSWORD


def _login_client(email: str, pw: str = TEST_PASSWORD) -> tuple[TestClient, int]:
    eid = create_editor(make_engine(), email, "AR", pw)
    c = TestClient(create_app())
    r = c.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return c, eid


def _cleanup(email: str) -> None:
    e = make_engine()
    with e.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM meta.edit_audit WHERE editor_id IN "
                "(SELECT id FROM meta.editors WHERE email = :e)"
            ),
            {"e": email},
        )
        conn.execute(
            text(
                "DELETE FROM meta.editor_sessions WHERE editor_id IN "
                "(SELECT id FROM meta.editors WHERE email = :e)"
            ),
            {"e": email},
        )
        conn.execute(text("DELETE FROM meta.editors WHERE email = :e"), {"e": email})
    e.dispose()


def test_audit_requires_auth():
    c = TestClient(create_app())
    r = c.get("/api/v1/audit")
    assert r.status_code == 401


def test_audit_returns_envelope_empty():
    email = "test-audit-empty@wordforge.dev"
    client, _ = _login_client(email)
    try:
        r = client.get("/api/v1/audit")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert isinstance(body["data"]["items"], list)
        assert "next_cursor" in body["data"]
    finally:
        _cleanup(email)


def test_audit_filter_by_word_id():
    email = "test-audit-byword@wordforge.dev"
    client, editor_id = _login_client(email)
    engine = make_engine()
    # seed 3 audit rows for word 100001 + 2 for word 100002
    with engine.begin() as conn:
        for wid, cnt in [(100001, 3), (100002, 2)]:
            for _ in range(cnt):
                conn.execute(
                    text(
                        "INSERT INTO meta.edit_audit "
                        "(word_id, field_path, target_id, op, old_value, new_value, editor_id) "
                        "VALUES (:w, 'meanings.cn_paraphrase', :tid, 'update', :ov, :nv, :eid)"
                    ),
                    {"w": wid, "tid": 1, "ov": '{"x": "a"}', "nv": '{"x": "b"}', "eid": editor_id},
                )
    try:
        r = client.get("/api/v1/audit?word_id=100001")
        assert r.status_code == 200
        items = r.json()["data"]["items"]
        assert len(items) == 3
        assert all(it["word_id"] == 100001 for it in items)
        assert all(it["editor"]["id"] == editor_id for it in items)
    finally:
        _cleanup(email)


def test_audit_invalid_cursor_400():
    email = "test-audit-bad@wordforge.dev"
    client, _ = _login_client(email)
    try:
        r = client.get("/api/v1/audit?cursor=garbage")
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "invalid_input"
    finally:
        _cleanup(email)
