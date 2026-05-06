"""PATCH /words/{id}: success / drift 409 / atomic rollback / 404 / auth."""
from fastapi.testclient import TestClient
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.app import create_app
from wordforge.web.services.editor_service import create_editor


def _login_client(email: str, pw: str = "pw1234ok") -> TestClient:
    create_editor(make_engine(), email, "PT", pw)
    c = TestClient(create_app())
    r = c.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return c


def _insert_word(form: str):
    """Insert test word + meaning, return (word_id, meaning_id).

    Cleans up any leftover from previous failed runs first.
    """
    e = make_engine()
    with e.begin() as conn:
        # Idempotent: remove stale data from prior failed runs
        conn.execute(
            text("DELETE FROM meta.edit_audit WHERE word_id IN "
                 "(SELECT word_id FROM domain.words WHERE form = :f)"), {"f": form})
        conn.execute(
            text("DELETE FROM serving.word_payload WHERE word_id IN "
                 "(SELECT word_id FROM domain.words WHERE form = :f)"), {"f": form})
        conn.execute(
            text("DELETE FROM domain.meanings WHERE word_id IN "
                 "(SELECT word_id FROM domain.words WHERE form = :f)"), {"f": form})
        conn.execute(text("DELETE FROM domain.words WHERE form = :f"), {"f": form})
        # Insert fresh
        row = conn.execute(
            text(
                "INSERT INTO domain.words (type, form, phonetic_us, phonetic_uk, source, status) "
                "VALUES (1, :f, '/t/', '/t/', 'human:test', 1) RETURNING word_id"
            ),
            {"f": form},
        ).first()
        wid = row.word_id
        mrow = conn.execute(
            text(
                "INSERT INTO domain.meanings (word_id, pos, cn_paraphrase, en_paraphrase, source) "
                "VALUES (:w, 1, 'cn_initial', 'en_initial', 'human:test') RETURNING meaning_id"
            ),
            {"w": wid},
        ).first()
        mid = mrow.meaning_id
    e.dispose()
    return wid, mid


def _cleanup_editor(email: str):
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


def _cleanup_word(word_id: int):
    e = make_engine()
    with e.begin() as conn:
        conn.execute(text("DELETE FROM meta.edit_audit WHERE word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM serving.word_payload WHERE word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM domain.meanings WHERE word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM domain.words WHERE word_id = :w"), {"w": word_id})
    e.dispose()


def test_patch_requires_auth():
    c = TestClient(create_app())
    r = c.patch("/api/v1/words/1", json={"changes": []})
    assert r.status_code == 401


def test_patch_not_found():
    email = "test-patch-404@wordforge.dev"
    client = _login_client(email)
    try:
        r = client.patch(
            "/api/v1/words/999999999",
            json={"changes": [{"field_path": "words.form", "target_id": None, "op": "update",
                               "old_value": "a", "new_value": "b"}]},
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"
    finally:
        _cleanup_editor(email)


def test_patch_success_updates_and_rebuilds_serving():
    email = "test-patch-ok@wordforge.dev"
    wid, mid = _insert_word("testword_patch_ok")
    client = _login_client(email)
    try:
        r = client.patch(
            f"/api/v1/words/{wid}",
            json={"changes": [
                {"field_path": "meanings.cn_paraphrase", "target_id": mid, "op": "update",
                 "old_value": "cn_initial", "new_value": "cn_patched"}
            ]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["applied"] == 1
        # DB value updated
        e = make_engine()
        with e.connect() as conn:
            row = conn.execute(
                text("SELECT cn_paraphrase FROM domain.meanings WHERE meaning_id = :m"),
                {"m": mid},
            ).first()
            serving = conn.execute(
                text("SELECT payload FROM serving.word_payload WHERE word_id = :w"),
                {"w": wid},
            ).first()
        assert row.cn_paraphrase == "cn_patched"
        # serving.word_payload rebuilt
        assert serving is not None
        e.dispose()
    finally:
        _cleanup_editor(email)
        _cleanup_word(wid)


def test_patch_drift_returns_409_and_rolls_back():
    email = "test-patch-drift@wordforge.dev"
    wid, mid = _insert_word("testword_patch_drift")
    client = _login_client(email)
    try:
        r = client.patch(
            f"/api/v1/words/{wid}",
            json={"changes": [
                {"field_path": "meanings.cn_paraphrase", "target_id": mid, "op": "update",
                 "old_value": "WRONG_OLD", "new_value": "should_not_apply"}
            ]},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "conflict"
        # DB unchanged
        e = make_engine()
        with e.connect() as conn:
            row = conn.execute(
                text("SELECT cn_paraphrase FROM domain.meanings WHERE meaning_id = :m"),
                {"m": mid},
            ).first()
            audit_cnt = conn.execute(
                text("SELECT COUNT(*) AS n FROM meta.edit_audit WHERE word_id = :w"),
                {"w": wid},
            ).first()
        assert row.cn_paraphrase == "cn_initial"
        assert audit_cnt.n == 0
        e.dispose()
    finally:
        _cleanup_editor(email)
        _cleanup_word(wid)


def test_patch_multi_change_second_drift_rolls_back_first():
    email = "test-patch-atomic@wordforge.dev"
    wid, mid = _insert_word("testword_patch_atomic")
    client = _login_client(email)
    try:
        r = client.patch(
            f"/api/v1/words/{wid}",
            json={"changes": [
                {"field_path": "meanings.cn_paraphrase", "target_id": mid, "op": "update",
                 "old_value": "cn_initial", "new_value": "cn_would_apply"},
                {"field_path": "meanings.en_paraphrase", "target_id": mid, "op": "update",
                 "old_value": "WRONG_OLD", "new_value": "en_never"},
            ]},
        )
        assert r.status_code == 409
        e = make_engine()
        with e.connect() as conn:
            row = conn.execute(
                text("SELECT cn_paraphrase, en_paraphrase FROM domain.meanings WHERE meaning_id = :m"),
                {"m": mid},
            ).first()
            audit_cnt = conn.execute(
                text("SELECT COUNT(*) AS n FROM meta.edit_audit WHERE word_id = :w"),
                {"w": wid},
            ).first()
        assert row.cn_paraphrase == "cn_initial"
        assert row.en_paraphrase == "en_initial"
        assert audit_cnt.n == 0
        e.dispose()
    finally:
        _cleanup_editor(email)
        _cleanup_word(wid)
