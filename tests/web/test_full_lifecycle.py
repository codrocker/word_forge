"""Full-lifecycle E2E: seed → login → search → detail → PATCH 4 tables → audit → status gate → drift 409."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from wordforge.db.engine import make_engine
from wordforge.web.app import create_app
from wordforge.web.services.editor_service import create_editor

_EMAIL = "test-lifecycle-e2e@wordforge.dev"
_FORM = "lifecycle_test_word"
_PW = "pw1234ok"


def _seed():
    """Seed a word + 2 meanings + 1 sentence per meaning + 1 mnemonic + 1 phrase.

    Returns (word_id, meaning_id_1, meaning_id_2, sentence_id_1, sentence_id_2, mnemonic_id, phrase_id).
    """
    e = make_engine()
    with e.begin() as conn:
        # Clean any leftover from prior failed runs
        wids = [
            r.word_id
            for r in conn.execute(
                text("SELECT word_id FROM domain.words WHERE form = :f"), {"f": _FORM}
            ).all()
        ]
        for wid in wids:
            conn.execute(text("DELETE FROM meta.edit_audit WHERE word_id = :w"), {"w": wid})
            conn.execute(text("DELETE FROM serving.word_payload WHERE word_id = :w"), {"w": wid})
            conn.execute(
                text(
                    "DELETE FROM domain.sentences WHERE meaning_id IN "
                    "(SELECT meaning_id FROM domain.meanings WHERE word_id = :w)"
                ),
                {"w": wid},
            )
            conn.execute(text("DELETE FROM domain.mnemonics WHERE word_id = :w"), {"w": wid})
            conn.execute(text("DELETE FROM domain.meanings WHERE word_id = :w"), {"w": wid})
            conn.execute(text("DELETE FROM domain.phrases WHERE owner_word_id = :w"), {"w": wid})
            conn.execute(text("DELETE FROM domain.words WHERE word_id = :w"), {"w": wid})

        # Insert word (status=1 so rebuild_word_payload will UPSERT)
        row = conn.execute(
            text(
                "INSERT INTO domain.words (type, form, phonetic_us, phonetic_uk, source, status, quality_flag) "
                "VALUES (1, :f, '/lf/', '/lf/', 'human:test', 1, 'none') RETURNING word_id"
            ),
            {"f": _FORM},
        ).first()
        wid = row.word_id

        # 2 meanings
        m1 = conn.execute(
            text(
                "INSERT INTO domain.meanings (word_id, pos, cn_paraphrase, en_paraphrase, source) "
                "VALUES (:w, 1, '原始中文释义1', 'original en paraphrase 1', 'human:test') RETURNING meaning_id"
            ),
            {"w": wid},
        ).first()
        m2 = conn.execute(
            text(
                "INSERT INTO domain.meanings (word_id, pos, cn_paraphrase, en_paraphrase, source) "
                "VALUES (:w, 2, '原始中文释义2', 'original en paraphrase 2', 'human:test') RETURNING meaning_id"
            ),
            {"w": wid},
        ).first()

        # 1 sentence per meaning
        s1 = conn.execute(
            text(
                "INSERT INTO domain.sentences (meaning_id, form, translation, source) "
                "VALUES (:m, 'The lifecycle test is running.', '生命周期测试正在运行。', 'human:test') "
                "RETURNING sentence_id"
            ),
            {"m": m1.meaning_id},
        ).first()
        s2 = conn.execute(
            text(
                "INSERT INTO domain.sentences (meaning_id, form, translation, source) "
                "VALUES (:m, 'Second sentence here.', '第二个例句。', 'human:test') "
                "RETURNING sentence_id"
            ),
            {"m": m2.meaning_id},
        ).first()

        # 1 mnemonic (JSONB content)
        mn = conn.execute(
            text(
                "INSERT INTO domain.mnemonics (word_id, type, content, source) "
                "VALUES (:w, 1, :c, 'human:test') RETURNING mnemonic_id"
            ),
            {"w": wid, "c": json.dumps({"story": "lifecycle mnemonic"}, ensure_ascii=False)},
        ).first()

        # 1 phrase (owner_word_id, NOT word_id)
        ph = conn.execute(
            text(
                "INSERT INTO domain.phrases (owner_word_id, form, meaning, source) "
                "VALUES (:w, 'lifecycle phrase', 'phrase meaning original', 'human:test') "
                "RETURNING phrase_id"
            ),
            {"w": wid},
        ).first()

    e.dispose()
    return wid, m1.meaning_id, m2.meaning_id, s1.sentence_id, s2.sentence_id, mn.mnemonic_id, ph.phrase_id


def _cleanup(word_id: int):
    """Remove all seed data + editor."""
    e = make_engine()
    with e.begin() as conn:
        conn.execute(text("DELETE FROM meta.edit_audit WHERE word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM serving.word_payload WHERE word_id = :w"), {"w": word_id})
        conn.execute(
            text(
                "DELETE FROM domain.sentences WHERE meaning_id IN "
                "(SELECT meaning_id FROM domain.meanings WHERE word_id = :w)"
            ),
            {"w": word_id},
        )
        conn.execute(text("DELETE FROM domain.mnemonics WHERE word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM domain.meanings WHERE word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM domain.phrases WHERE owner_word_id = :w"), {"w": word_id})
        conn.execute(text("DELETE FROM domain.words WHERE word_id = :w"), {"w": word_id})
        # Editor cleanup
        conn.execute(
            text(
                "DELETE FROM meta.edit_audit WHERE editor_id IN "
                "(SELECT id FROM meta.editors WHERE email = :e)"
            ),
            {"e": _EMAIL},
        )
        conn.execute(
            text(
                "DELETE FROM meta.editor_sessions WHERE editor_id IN "
                "(SELECT id FROM meta.editors WHERE email = :e)"
            ),
            {"e": _EMAIL},
        )
        conn.execute(text("DELETE FROM meta.editors WHERE email = :e"), {"e": _EMAIL})
    e.dispose()


def test_full_edit_lifecycle():
    """One test covering: seed → login → search → detail → PATCH cross-table → audit → status gate → drift."""
    wid, mid1, mid2, sid1, sid2, mnid, phid = _seed()
    try:
        # --- Create editor + login ---
        create_editor(make_engine(), _EMAIL, "E2E", _PW)
        client = TestClient(create_app())
        r = client.post("/api/v1/auth/login", json={"email": _EMAIL, "password": _PW})
        assert r.status_code == 200, f"login failed: {r.text}"

        # --- Search: GET /words?q=<form> finds the word ---
        r = client.get(f"/api/v1/words?q={_FORM}")
        assert r.status_code == 200, f"search failed: {r.text}"
        items = r.json()["data"]["items"]
        assert any(
            it["word_id"] == wid for it in items
        ), f"word_id={wid} not found in search results: {items}"

        # --- Detail: GET /words/{id} returns all 5 sub-tables non-empty ---
        r = client.get(f"/api/v1/words/{wid}")
        assert r.status_code == 200, f"detail failed: {r.text}"
        detail = r.json()["data"]
        assert detail["word"]["word_id"] == wid
        assert len(detail["meanings"]) == 2, f"expected 2 meanings, got {len(detail['meanings'])}"
        assert len(detail["sentences"]) == 2, f"expected 2 sentences, got {len(detail['sentences'])}"
        assert len(detail["mnemonics"]) >= 1, f"expected >=1 mnemonic, got {len(detail['mnemonics'])}"
        assert len(detail["phrases"]) >= 1, f"expected >=1 phrase, got {len(detail['phrases'])}"

        # --- PATCH: change 4 fields across 4 tables ---
        patch_body = {
            "changes": [
                {
                    "field_path": "words.form",
                    "target_id": None,
                    "op": "update",
                    "old_value": _FORM,
                    "new_value": "lifecycle_patched",
                },
                {
                    "field_path": "meanings.cn_paraphrase",
                    "target_id": mid1,
                    "op": "update",
                    "old_value": "原始中文释义1",
                    "new_value": "修改后中文释义",
                },
                {
                    "field_path": "sentences.translation",
                    "target_id": sid1,
                    "op": "update",
                    "old_value": "生命周期测试正在运行。",
                    "new_value": "修改后的翻译。",
                },
                {
                    "field_path": "phrases.meaning",
                    "target_id": phid,
                    "op": "update",
                    "old_value": "phrase meaning original",
                    "new_value": "phrase meaning patched",
                },
            ]
        }
        r = client.patch(f"/api/v1/words/{wid}", json=patch_body)
        assert r.status_code == 200, f"PATCH failed: {r.text}"
        assert r.json()["data"]["applied"] == 4

        # --- Verify DB: all 4 changes persisted ---
        r = client.get(f"/api/v1/words/{wid}")
        assert r.status_code == 200
        detail = r.json()["data"]
        assert detail["word"]["form"] == "lifecycle_patched", (
            f"words.form not updated: {detail['word']['form']}"
        )
        m1_data = next(m for m in detail["meanings"] if m["meaning_id"] == mid1)
        assert m1_data["cn_paraphrase"] == "修改后中文释义", (
            f"meanings.cn_paraphrase not updated: {m1_data['cn_paraphrase']}"
        )
        s1_data = next(s for s in detail["sentences"] if s["sentence_id"] == sid1)
        assert s1_data["translation"] == "修改后的翻译。", (
            f"sentences.translation not updated: {s1_data['translation']}"
        )
        ph_data = next(p for p in detail["phrases"] if p["phrase_id"] == phid)
        assert ph_data["meaning"] == "phrase meaning patched", (
            f"phrases.meaning not updated: {ph_data['meaning']}"
        )

        # --- Audit: 4 records with correct field_paths ---
        e = make_engine()
        with e.connect() as conn:
            audits = conn.execute(
                text(
                    "SELECT field_path, op FROM meta.edit_audit "
                    "WHERE word_id = :w ORDER BY id"
                ),
                {"w": wid},
            ).mappings().all()
        e.dispose()
        audit_paths = [a["field_path"] for a in audits]
        assert len(audits) == 4, f"expected 4 audit rows, got {len(audits)}: {audit_paths}"
        expected_paths = {"words.form", "meanings.cn_paraphrase", "sentences.translation", "phrases.meaning"}
        assert set(audit_paths) == expected_paths, (
            f"audit field_paths mismatch: got {set(audit_paths)}, expected {expected_paths}"
        )
        assert all(a["op"] == "update" for a in audits)

        # --- Status gate: status 1→2 removes serving.word_payload ---
        r = client.post(f"/api/v1/words/{wid}/status", json={"old_value": 1, "new_value": 2})
        assert r.status_code == 200, f"status 1→2 failed: {r.text}"
        e = make_engine()
        with e.connect() as conn:
            serving = conn.execute(
                text("SELECT 1 FROM serving.word_payload WHERE word_id = :w"), {"w": wid}
            ).first()
        e.dispose()
        assert serving is None, "status=2 should DELETE from serving.word_payload"

        # --- Drift 409: status is now 2, sending old_value=1 should fail ---
        r = client.post(f"/api/v1/words/{wid}/status", json={"old_value": 1, "new_value": 0})
        assert r.status_code == 409, f"expected drift 409, got {r.status_code}: {r.text}"
        assert r.json()["error"]["code"] == "conflict"

    finally:
        _cleanup(wid)
