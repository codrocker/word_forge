import pytest
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.auth import verify_password
from wordforge.web.services.editor_service import (
    create_editor,
    deactivate_editor,
    list_editors,
)


@pytest.fixture
def engine():
    e = make_engine()
    yield e
    # cleanup: delete any test emails created
    with e.begin() as conn:
        conn.execute(
            text("DELETE FROM meta.editors WHERE email LIKE 'test-editor-%@wordforge.local'")
        )
    e.dispose()


def test_create_editor_stores_hashed_password(engine):
    new_id = create_editor(engine, "test-editor-create@wordforge.local", "TC", "secret123")
    assert new_id > 0
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT password_hash FROM meta.editors WHERE id = :i"),
            {"i": new_id},
        ).first()
    assert row.password_hash != "secret123", "plaintext must not be stored"
    assert verify_password(row.password_hash, "secret123"), "hash must verify correct pw"
    assert not verify_password(row.password_hash, "wrong"), "wrong pw must fail"


def test_create_duplicate_email_raises(engine):
    from sqlalchemy.exc import IntegrityError

    create_editor(engine, "test-editor-dup@wordforge.local", "D1", "p")
    with pytest.raises(IntegrityError):
        create_editor(engine, "test-editor-dup@wordforge.local", "D2", "p")


def test_list_shows_new_editor(engine):
    create_editor(engine, "test-editor-list@wordforge.local", "L", "p")
    rows = [r for r in list_editors(engine) if r["email"] == "test-editor-list@wordforge.local"]
    assert len(rows) == 1
    assert rows[0]["is_active"] is True
    assert rows[0]["display_name"] == "L"


def test_deactivate_flips_is_active(engine):
    create_editor(engine, "test-editor-deact@wordforge.local", "DA", "p")
    deactivate_editor(engine, "test-editor-deact@wordforge.local")
    rows = [r for r in list_editors(engine) if r["email"] == "test-editor-deact@wordforge.local"]
    assert rows[0]["is_active"] is False
