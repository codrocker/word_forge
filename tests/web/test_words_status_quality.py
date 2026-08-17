"""POST /status and /quality: success / drift 409 / 404 / auth / serving gate."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.app import create_app
from wordforge.web.services.editor_service import create_editor
from tests.web.conftest import TEST_PASSWORD


def _setup(email: str, form: str, initial_status: int = 1, initial_quality: str = "none"):
    e = make_engine()
    create_editor(e, email, "SQ", TEST_PASSWORD)
    with e.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO domain.words "
                "(type, form, phonetic_us, phonetic_uk, source, status, quality_flag) "
                "VALUES (1, :form, '/t/', '/t/', 'human:test', :s, :q) "
                "RETURNING word_id"
            ),
            {"form": form, "s": initial_status, "q": initial_quality},
        ).first()
        wid = row.word_id
        if initial_status == 1:
            conn.execute(
                text(
                    "INSERT INTO serving.word_payload "
                    "(word_id, form, type, payload, payload_schema_version, updated_at) "
                    "VALUES (:w, :form, 1, :p, 1, now())"
                ),
                {"w": wid, "form": form, "p": '{"status": 1}'},
            )
    c = TestClient(create_app())
    r = c.post("/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return c, wid


def _cleanup(email: str, word_id: int):
    e = make_engine()
    with e.begin() as conn:
        conn.execute(text("DELETE FROM meta.edit_audit WHERE word_id = :w"), {"w": word_id})
        conn.execute(
            text(
                "DELETE FROM meta.editor_sessions WHERE editor_id IN "
                "(SELECT id FROM meta.editors WHERE email = :e)"
            ),
            {"e": email},
        )
        conn.execute(text("DELETE FROM serving.word_payload WHERE word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM meta.editors WHERE email = :e"), {"e": email})
        conn.execute(text("DELETE FROM domain.words WHERE word_id = :w"), {"w": word_id})
    e.dispose()


def test_status_change_1_to_2_deletes_serving():
    email = "test-status-archive@wordforge.dev"
    client, wid = _setup(email, "testsq_archive", initial_status=1)
    try:
        r = client.post(
            f"/api/v1/words/{wid}/status", json={"old_value": 1, "new_value": 2}
        )
        assert r.status_code == 200, r.text
        e = make_engine()
        with e.connect() as conn:
            word = conn.execute(
                text("SELECT status FROM domain.words WHERE word_id = :w"), {"w": wid}
            ).first()
            serving = conn.execute(
                text("SELECT 1 FROM serving.word_payload WHERE word_id = :w"), {"w": wid}
            ).first()
            audit = conn.execute(
                text(
                    "SELECT field_path, old_value, new_value FROM meta.edit_audit "
                    "WHERE word_id = :w"
                ),
                {"w": wid},
            ).mappings().first()
        assert word.status == 2
        assert serving is None, "status=2 should remove from serving read model"
        assert audit["field_path"] == "words.status"
        assert audit["old_value"] == 1
        assert audit["new_value"] == 2
        e.dispose()
    finally:
        _cleanup(email, wid)


def test_status_drift_returns_409():
    email = "test-status-drift@wordforge.dev"
    client, wid = _setup(email, "testsq_drift", initial_status=1)
    try:
        r = client.post(
            f"/api/v1/words/{wid}/status", json={"old_value": 0, "new_value": 1}
        )  # DB actual is 1, old_value passed 0 -> drift
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"
        # no side effects
        e = make_engine()
        with e.connect() as conn:
            word = conn.execute(
                text("SELECT status FROM domain.words WHERE word_id = :w"), {"w": wid}
            ).first()
            audit_cnt = conn.execute(
                text("SELECT COUNT(*) AS n FROM meta.edit_audit WHERE word_id = :w"),
                {"w": wid},
            ).first()
        assert word.status == 1
        assert audit_cnt.n == 0
        e.dispose()
    finally:
        _cleanup(email, wid)


def test_quality_change_success():
    email = "test-quality-ok@wordforge.dev"
    client, wid = _setup(email, "testsq_qualok", initial_status=1, initial_quality="none")
    try:
        r = client.post(
            f"/api/v1/words/{wid}/quality",
            json={"old_value": "none", "new_value": "suspect"},
        )
        assert r.status_code == 200
        e = make_engine()
        with e.connect() as conn:
            word = conn.execute(
                text("SELECT quality_flag FROM domain.words WHERE word_id = :w"),
                {"w": wid},
            ).first()
            audit = conn.execute(
                text(
                    "SELECT field_path, old_value, new_value FROM meta.edit_audit "
                    "WHERE word_id = :w"
                ),
                {"w": wid},
            ).mappings().first()
        assert word.quality_flag == "suspect"
        assert audit["field_path"] == "words.quality_flag"
        assert audit["old_value"] == "none"
        assert audit["new_value"] == "suspect"
        e.dispose()
    finally:
        _cleanup(email, wid)


def test_quality_drift_returns_409():
    email = "test-quality-drift@wordforge.dev"
    client, wid = _setup(email, "testsq_qdrift", initial_status=1, initial_quality="none")
    try:
        r = client.post(
            f"/api/v1/words/{wid}/quality",
            json={"old_value": "suspect", "new_value": "fixed"},
        )  # DB is none, old_value passed suspect -> drift
        assert r.status_code == 409
        e = make_engine()
        with e.connect() as conn:
            word = conn.execute(
                text("SELECT quality_flag FROM domain.words WHERE word_id = :w"),
                {"w": wid},
            ).first()
        assert word.quality_flag == "none"
        e.dispose()
    finally:
        _cleanup(email, wid)


def test_status_change_requires_auth():
    client = TestClient(create_app())
    r = client.post("/api/v1/words/1/status", json={"old_value": 0, "new_value": 1})
    assert r.status_code == 401
