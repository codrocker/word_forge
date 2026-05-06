"""audit_service.write_audit: update/insert/delete + target_id semantics."""
import pytest
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.services.audit_service import write_audit
from wordforge.web.services.editor_service import create_editor


@pytest.fixture
def engine_and_editor():
    e = make_engine()
    eid = create_editor(e, "test-audit-svc@wordforge.local", "AS", "pw1234ok")
    yield e, eid
    with e.begin() as conn:
        conn.execute(text("DELETE FROM meta.edit_audit WHERE editor_id = :i"), {"i": eid})
        conn.execute(text("DELETE FROM meta.editors WHERE id = :i"), {"i": eid})
    e.dispose()


def _fetch_audit(engine, editor_id, limit=10):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT word_id, field_path, target_id, op, old_value, new_value "
                "FROM meta.edit_audit WHERE editor_id = :i ORDER BY id"
            ),
            {"i": editor_id},
        ).mappings().all()
    return [dict(r) for r in rows]


def test_write_update_with_sub_table_target_id(engine_and_editor):
    engine, editor_id = engine_and_editor
    with engine.begin() as conn:
        write_audit(
            conn, word_id=100001,
            field_path="meanings.cn_paraphrase", target_id=777,
            op="update",
            old_value={"v": "old"}, new_value={"v": "new"},
            editor_id=editor_id,
        )
    rows = _fetch_audit(engine, editor_id)
    assert len(rows) == 1
    r = rows[0]
    assert r["word_id"] == 100001
    assert r["field_path"] == "meanings.cn_paraphrase"
    assert r["target_id"] == 777
    assert r["op"] == "update"
    assert r["old_value"] == {"v": "old"}
    assert r["new_value"] == {"v": "new"}


def test_write_update_on_words_table_has_null_target_id(engine_and_editor):
    engine, editor_id = engine_and_editor
    with engine.begin() as conn:
        write_audit(
            conn, word_id=100001,
            field_path="words.status", target_id=None,
            op="update",
            old_value=0, new_value=1,
            editor_id=editor_id,
        )
    rows = _fetch_audit(engine, editor_id)
    assert len(rows) == 1
    assert rows[0]["target_id"] is None
    assert rows[0]["field_path"] == "words.status"


def test_write_insert_has_null_old_value(engine_and_editor):
    engine, editor_id = engine_and_editor
    with engine.begin() as conn:
        write_audit(
            conn, word_id=100001,
            field_path="meanings", target_id=888,
            op="insert",
            old_value=None, new_value={"pos": 1, "cn_paraphrase": "x"},
            editor_id=editor_id,
        )
    rows = _fetch_audit(engine, editor_id)
    assert rows[0]["op"] == "insert"
    assert rows[0]["old_value"] is None
    assert rows[0]["new_value"] == {"pos": 1, "cn_paraphrase": "x"}


def test_write_delete_has_null_new_value(engine_and_editor):
    engine, editor_id = engine_and_editor
    with engine.begin() as conn:
        write_audit(
            conn, word_id=100001,
            field_path="sentences", target_id=999,
            op="delete",
            old_value={"form": "deleted sentence"}, new_value=None,
            editor_id=editor_id,
        )
    rows = _fetch_audit(engine, editor_id)
    assert rows[0]["op"] == "delete"
    assert rows[0]["old_value"] == {"form": "deleted sentence"}
    assert rows[0]["new_value"] is None


def test_write_rejects_invalid_op(engine_and_editor):
    engine, editor_id = engine_and_editor
    with engine.begin() as conn:
        with pytest.raises(ValueError, match="invalid op"):
            write_audit(
                conn, word_id=100001, field_path="x", target_id=None,
                op="nuke",  # type: ignore
                old_value=None, new_value=None, editor_id=editor_id,
            )
