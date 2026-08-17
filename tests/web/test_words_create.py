"""POST /words: create word + sub-tables, UNIQUE 409, source stamp, strip."""
from fastapi.testclient import TestClient
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.app import create_app
from wordforge.web.services.editor_service import create_editor
from tests.web.conftest import TEST_PASSWORD


def _login_client(email: str, pw: str = TEST_PASSWORD) -> TestClient:
    create_editor(make_engine(), email, "PT", pw)
    c = TestClient(create_app())
    r = c.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return c


def _cleanup(email: str, form: str):
    e = make_engine()
    with e.begin() as conn:
        # Get word_ids for this form
        wids = [
            r.word_id
            for r in conn.execute(
                text("SELECT word_id FROM domain.words WHERE form = :f"), {"f": form}
            ).all()
        ]
        for wid in wids:
            conn.execute(
                text("DELETE FROM meta.edit_audit WHERE word_id = :w"), {"w": wid}
            )
            conn.execute(
                text("DELETE FROM serving.word_payload WHERE word_id = :w"), {"w": wid}
            )
            conn.execute(
                text(
                    "DELETE FROM domain.sentences WHERE meaning_id IN "
                    "(SELECT meaning_id FROM domain.meanings WHERE word_id = :w)"
                ),
                {"w": wid},
            )
            conn.execute(
                text("DELETE FROM domain.mnemonics WHERE word_id = :w"), {"w": wid}
            )
            conn.execute(
                text("DELETE FROM domain.meanings WHERE word_id = :w"), {"w": wid}
            )
            conn.execute(
                text("DELETE FROM domain.phrases WHERE owner_word_id = :w"), {"w": wid}
            )
            conn.execute(
                text("DELETE FROM domain.words WHERE word_id = :w"), {"w": wid}
            )
        # Editor cleanup
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


def test_create_word_empty_shell():
    """201: create word with no sub-tables, status=0, source=human:web."""
    email = "test-create-empty@wordforge.dev"
    form = "testcreateempty"
    _cleanup(email, form)
    client = _login_client(email)
    try:
        r = client.post(
            "/api/v1/words",
            json={"form": form, "type": 1},
        )
        assert r.status_code == 201, r.text
        data = r.json()["data"]
        assert data["word_id"] > 0
        assert data["created"] is True
        # Verify DB
        e = make_engine()
        with e.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, quality_flag, source FROM domain.words WHERE word_id = :w"
                ),
                {"w": data["word_id"]},
            ).first()
        assert row.status == 0
        assert row.quality_flag == "none"
        assert row.source == "human:web"
        e.dispose()
    finally:
        _cleanup(email, form)


def test_create_word_with_subtables():
    """201: meaning + sentence created, source forced human:web, audit rows."""
    email = "test-create-sub@wordforge.dev"
    form = "testcreatesub"
    _cleanup(email, form)
    client = _login_client(email)
    try:
        r = client.post(
            "/api/v1/words",
            json={
                "form": form,
                "type": 1,
                "meanings": [{"pos": 1, "cn_paraphrase": "测试释义"}],
                "sentences": [
                    {
                        "meaning_index": 0,
                        "form": "This is a test.",
                        "translation": "这是一个测试。",
                    }
                ],
            },
        )
        assert r.status_code == 201, r.text
        word_id = r.json()["data"]["word_id"]
        # Verify sub-tables
        e = make_engine()
        with e.connect() as conn:
            meaning = conn.execute(
                text(
                    "SELECT source, cn_paraphrase FROM domain.meanings WHERE word_id = :w"
                ),
                {"w": word_id},
            ).first()
            assert meaning.source == "human:web"
            assert meaning.cn_paraphrase == "测试释义"
            sentence = conn.execute(
                text(
                    "SELECT s.source FROM domain.sentences s "
                    "JOIN domain.meanings m ON s.meaning_id = m.meaning_id "
                    "WHERE m.word_id = :w"
                ),
                {"w": word_id},
            ).first()
            assert sentence.source == "human:web"
            # Audit: words + meanings + sentences = 3 rows
            audit_cnt = conn.execute(
                text(
                    "SELECT COUNT(*) AS n FROM meta.edit_audit WHERE word_id = :w"
                ),
                {"w": word_id},
            ).first()
            assert audit_cnt.n == 3
        e.dispose()
    finally:
        _cleanup(email, form)


def test_create_word_conflict_returns_409():
    """409: form+type already exists returns existing word_id."""
    email = "test-create-conflict@wordforge.dev"
    form = "testcreateconflict"
    _cleanup(email, form)
    client = _login_client(email)
    try:
        # First create succeeds
        r1 = client.post(
            "/api/v1/words",
            json={"form": form, "type": 1},
        )
        assert r1.status_code == 201, r1.text
        first_id = r1.json()["data"]["word_id"]
        # Second create same form+type → 409
        r2 = client.post(
            "/api/v1/words",
            json={"form": form, "type": 1},
        )
        assert r2.status_code == 409, r2.text
        err = r2.json()["error"]
        assert err["code"] == "conflict"
        assert err["details"]["word_id"] == first_id
        assert err["details"]["reason"] == "already_exists"
    finally:
        _cleanup(email, form)


def test_create_word_source_override():
    """Frontend-supplied source is ignored; always human:web."""
    email = "test-create-source@wordforge.dev"
    form = "testcreatesource"
    _cleanup(email, form)
    client = _login_client(email)
    try:
        # Even if the request body sneaks in a "source" field, the DB should have human:web
        r = client.post(
            "/api/v1/words",
            json={
                "form": form,
                "type": 1,
                "meanings": [
                    {"pos": 1, "cn_paraphrase": "src test", "source": "pipeline:fake"}
                ],
            },
        )
        assert r.status_code == 201, r.text
        word_id = r.json()["data"]["word_id"]
        e = make_engine()
        with e.connect() as conn:
            wrow = conn.execute(
                text("SELECT source FROM domain.words WHERE word_id = :w"),
                {"w": word_id},
            ).first()
            mrow = conn.execute(
                text("SELECT source FROM domain.meanings WHERE word_id = :w"),
                {"w": word_id},
            ).first()
        assert wrow.source == "human:web"
        assert mrow.source == "human:web"
        e.dispose()
    finally:
        _cleanup(email, form)


def test_create_word_form_stripped():
    """Leading/trailing whitespace stripped from form."""
    email = "test-create-strip@wordforge.dev"
    form_raw = "  testcreatestrip  "
    form_clean = "testcreatestrip"
    _cleanup(email, form_clean)
    client = _login_client(email)
    try:
        r = client.post(
            "/api/v1/words",
            json={"form": form_raw, "type": 1},
        )
        assert r.status_code == 201, r.text
        word_id = r.json()["data"]["word_id"]
        e = make_engine()
        with e.connect() as conn:
            row = conn.execute(
                text("SELECT form FROM domain.words WHERE word_id = :w"),
                {"w": word_id},
            ).first()
        assert row.form == form_clean
        e.dispose()
    finally:
        _cleanup(email, form_clean)
