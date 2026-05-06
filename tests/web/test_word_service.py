"""apply_web_changes: success / drift / atomic rollback / words target_id=null."""
import pytest
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.reviewer.patch import PatchDriftError
from wordforge.web.services.word_service import apply_web_changes
from wordforge.web.services.editor_service import create_editor


@pytest.fixture
def engine_editor_word():
    e = make_engine()
    eid = create_editor(e, "test-ws@wordforge.local", "WS", "pw1234ok")
    with e.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO domain.words (type, form, phonetic_us, phonetic_uk, source, status) "
                "VALUES (1, 'testword_apply', '/tu/', '/tk/', 'human:test', 1) "
                "RETURNING word_id"
            )
        ).first()
        wid = row.word_id
        mrow = conn.execute(
            text(
                "INSERT INTO domain.meanings (word_id, pos, cn_paraphrase, en_paraphrase, source) "
                "VALUES (:w, 1, 'old_cn', 'old_en', 'human:test') RETURNING meaning_id"
            ),
            {"w": wid},
        ).first()
        mid = mrow.meaning_id
    yield e, eid, wid, mid
    with e.begin() as conn:
        conn.execute(text("DELETE FROM meta.edit_audit WHERE editor_id = :i"), {"i": eid})
        conn.execute(text("DELETE FROM domain.meanings WHERE meaning_id = :m"), {"m": mid})
        conn.execute(text("DELETE FROM domain.words WHERE word_id = :w"), {"w": wid})
        conn.execute(text("DELETE FROM meta.editors WHERE id = :i"), {"i": eid})
    e.dispose()


def test_update_meaning_cn_paraphrase_success(engine_editor_word):
    engine, editor_id, word_id, meaning_id = engine_editor_word
    with engine.begin() as conn:
        n = apply_web_changes(
            conn,
            word_id=word_id,
            editor_id=editor_id,
            changes=[
                {
                    "field_path": "meanings.cn_paraphrase",
                    "target_id": meaning_id,
                    "op": "update",
                    "old_value": "old_cn",
                    "new_value": "new_cn",
                }
            ],
        )
    assert n == 1
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT cn_paraphrase FROM domain.meanings WHERE meaning_id = :m"),
            {"m": meaning_id},
        ).first()
        audit = conn.execute(
            text(
                "SELECT field_path, target_id, op, old_value, new_value "
                "FROM meta.edit_audit WHERE editor_id = :i"
            ),
            {"i": editor_id},
        ).mappings().all()
    assert row.cn_paraphrase == "new_cn"
    assert len(audit) == 1
    a = audit[0]
    assert a["field_path"] == "meanings.cn_paraphrase"
    assert a["target_id"] == meaning_id
    assert a["op"] == "update"
    assert a["old_value"] == "old_cn"
    assert a["new_value"] == "new_cn"


def test_drift_raises_and_rolls_back(engine_editor_word):
    engine, editor_id, word_id, meaning_id = engine_editor_word
    with pytest.raises(PatchDriftError):
        with engine.begin() as conn:
            apply_web_changes(
                conn,
                word_id=word_id,
                editor_id=editor_id,
                changes=[
                    {
                        "field_path": "meanings.cn_paraphrase",
                        "target_id": meaning_id,
                        "op": "update",
                        "old_value": "WRONG_OLD",
                        "new_value": "new_cn",
                    }
                ],
            )
    # after rollback: meaning unchanged, no audit
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT cn_paraphrase FROM domain.meanings WHERE meaning_id = :m"),
            {"m": meaning_id},
        ).first()
        count = conn.execute(
            text("SELECT COUNT(*) AS n FROM meta.edit_audit WHERE editor_id = :i"),
            {"i": editor_id},
        ).first()
    assert row.cn_paraphrase == "old_cn"
    assert count.n == 0


def test_multi_changes_second_drift_rolls_back_first(engine_editor_word):
    """Critical atomic test: 2 changes, second drifts -> both changes rolled back."""
    engine, editor_id, word_id, meaning_id = engine_editor_word
    with pytest.raises(PatchDriftError):
        with engine.begin() as conn:
            apply_web_changes(
                conn,
                word_id=word_id,
                editor_id=editor_id,
                changes=[
                    {
                        "field_path": "meanings.cn_paraphrase",
                        "target_id": meaning_id,
                        "op": "update",
                        "old_value": "old_cn",
                        "new_value": "CHANGED_CN",
                    },
                    {
                        "field_path": "meanings.en_paraphrase",
                        "target_id": meaning_id,
                        "op": "update",
                        "old_value": "WRONG_OLD",
                        "new_value": "X",
                    },
                ],
            )
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT cn_paraphrase, en_paraphrase FROM domain.meanings WHERE meaning_id = :m"),
            {"m": meaning_id},
        ).first()
        count = conn.execute(
            text("SELECT COUNT(*) AS n FROM meta.edit_audit WHERE editor_id = :i"),
            {"i": editor_id},
        ).first()
    assert row.cn_paraphrase == "old_cn"
    assert row.en_paraphrase == "old_en"
    assert count.n == 0


def test_update_words_form_uses_null_target_id(engine_editor_word):
    engine, editor_id, word_id, _ = engine_editor_word
    with engine.begin() as conn:
        n = apply_web_changes(
            conn,
            word_id=word_id,
            editor_id=editor_id,
            changes=[
                {
                    "field_path": "words.form",
                    "target_id": None,
                    "op": "update",
                    "old_value": "testword_apply",
                    "new_value": "renamed_apply",
                }
            ],
        )
    assert n == 1
    with engine.connect() as conn:
        audit = conn.execute(
            text("SELECT target_id, field_path FROM meta.edit_audit WHERE editor_id = :i"),
            {"i": editor_id},
        ).mappings().first()
    assert audit["target_id"] is None
    assert audit["field_path"] == "words.form"


def test_unknown_field_path_raises_value_error(engine_editor_word):
    engine, editor_id, word_id, _ = engine_editor_word
    with pytest.raises(ValueError, match="unknown field_path"):
        with engine.begin() as conn:
            apply_web_changes(
                conn,
                word_id=word_id,
                editor_id=editor_id,
                changes=[
                    {
                        "field_path": "words.password_hash",
                        "target_id": None,
                        "op": "update",
                        "old_value": "a",
                        "new_value": "b",
                    }
                ],
            )
