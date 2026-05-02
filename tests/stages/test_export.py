"""P5c Task 1: ExportStage — Case A/B/C + preflight + ConcurrentModificationError."""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from wordforge.config import StageConfig
from wordforge.db.engine import make_engine
from wordforge.ingest import ingest_words
from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.stages.export import ConcurrentModificationError, ExportStage


def _make_stage(engine):
    artifacts = StageArtifactStore(engine)
    cfg = StageConfig(parser_version="1", cost_estimate_usd=0.0)
    return ExportStage(engine=engine, artifacts=artifacts, config=cfg), artifacts


def _seed_word(engine, form="apple", batch_id="B_EX"):
    ingest_words(engine, raw_forms=[form], batch_id=batch_id)
    with engine.begin() as conn:
        return conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id = :b"),
            {"b": batch_id},
        ).scalar_one()


_PIPELINE_SOURCE = "pipeline:anthropic:claude-opus-4:paraphrase_v1"


def _seed_all_upstreams(artifacts, wid):
    """Seed all 7 upstream artifacts needed by export."""
    artifacts.upsert(
        word_id=wid,
        stage_name="fetch_dict",
        fingerprint="fp_fd",
        payload={"raw_json": {"simple": {}}, "form": "apple"},
        source="pipeline:youdao:fetch_dict_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="fp_para",
        payload={"meanings": [{"pos": "n", "cn": "苹果", "en": "a fruit"}]},
        source=_PIPELINE_SOURCE,
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="phonetic",
        fingerprint="fp_phon",
        payload={"phonetic_us": "ˈæpl", "phonetic_uk": "ˈæpəl", "audio_us": None, "audio_uk": None},
        source="pipeline:local:phonetic_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="derivatives",
        fingerprint="fp_deriv",
        payload={"word_forms": {"plural": "apples"}, "per_meaning": []},
        source="pipeline:anthropic:claude-opus-4:derivatives_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="examples",
        fingerprint="fp_ex",
        payload={"per_meaning": [{"meaning_index": 0, "examples": [{"en": "I ate an apple."}]}]},
        source="pipeline:anthropic:claude-opus-4:examples_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="mnemonic",
        fingerprint="fp_mnem",
        payload={"mnemonic": "Apple谐音阿婆", "kind": "phonetic"},
        source="pipeline:anthropic:claude-opus-4:mnemonic_v1",
    )
    artifacts.upsert(
        word_id=wid,
        stage_name="quality_gate",
        fingerprint="fp_qg",
        payload={"passed": True, "failed_rules": [], "checked_at": "2026-04-29T00:00:00+00:00"},
        source="pipeline:local:quality_gate_v1",
    )


def test_export_case_a_new_word_inserts(at_head):
    """Case A: no existing domain.words row → INSERT."""
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_EX_A1")
    _seed_all_upstreams(artifacts, wid)

    result = asyncio.run(stage.run_one(word_id=wid))

    assert result.payload["case"] == "A"
    assert result.payload["app_word_id"] > 0
    assert result.source == "pipeline:local:export_v1"

    # Verify domain.words row
    with engine.begin() as conn:
        row = conn.execute(
            sa.text(
                "SELECT form, type, phonetic_us, plural, source FROM domain.words WHERE word_id = :w"
            ),
            {"w": result.payload["app_word_id"]},
        ).one()
    assert row[0] == "apple"
    assert row[1] == 1  # single word
    assert row[2] == "ˈæpl"
    assert row[3] == "apples"
    assert row[4] == _PIPELINE_SOURCE


def test_export_case_a_reads_all_upstreams(at_head):
    """Export inserts meanings + mnemonics from upstream artifacts."""
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_EX_A2")
    _seed_all_upstreams(artifacts, wid)

    result = asyncio.run(stage.run_one(word_id=wid))
    app_wid = result.payload["app_word_id"]

    with engine.begin() as conn:
        meanings = conn.execute(
            sa.text("SELECT cn_paraphrase, pos FROM domain.meanings WHERE word_id = :w"),
            {"w": app_wid},
        ).all()
        mnemonics = conn.execute(
            sa.text("SELECT type, content FROM domain.mnemonics WHERE word_id = :w"),
            {"w": app_wid},
        ).all()

    assert len(meanings) == 1
    assert meanings[0][0] == "苹果"
    assert meanings[0][1] == 1  # pos_map["n"] = 1

    assert len(mnemonics) == 1
    assert mnemonics[0][0] == 1  # type = 1
    import json

    content = json.loads(mnemonics[0][1]) if isinstance(mnemonics[0][1], str) else mnemonics[0][1]
    assert content["text"] == "Apple谐音阿婆"
    assert content["kind"] == "phonetic"


def test_export_case_b_upserts_and_replaces_children(at_head):
    """Case B: existing pipeline: row → UPSERT + DELETE children + re-INSERT."""
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_EX_B1")
    _seed_all_upstreams(artifacts, wid)

    # First run — Case A: creates the row
    result1 = asyncio.run(stage.run_one(word_id=wid))
    assert result1.payload["case"] == "A"
    app_wid = result1.payload["app_word_id"]

    # Modify upstream paraphrase to have 2 meanings
    artifacts.upsert(
        word_id=wid,
        stage_name="paraphrase",
        fingerprint="fp_para_v2",
        payload={
            "meanings": [
                {"pos": "n", "cn": "苹果", "en": "a fruit"},
                {"pos": "n", "cn": "苹果公司", "en": "Apple Inc."},
            ]
        },
        source=_PIPELINE_SOURCE,
    )

    # Second run — Case B: same form+type exists with pipeline: source
    result2 = asyncio.run(stage.run_one(word_id=wid))
    assert result2.payload["case"] == "B"
    assert result2.payload["app_word_id"] == app_wid  # same word_id

    # Verify meanings replaced (2 now instead of 1)
    with engine.begin() as conn:
        meanings = conn.execute(
            sa.text(
                "SELECT cn_paraphrase FROM domain.meanings WHERE word_id = :w ORDER BY cn_paraphrase"
            ),
            {"w": app_wid},
        ).all()
    assert len(meanings) == 2
    assert meanings[0][0] == "苹果"
    assert meanings[1][0] == "苹果公司"


def test_export_case_b_preflight_pass(at_head):
    """Case B preflight passes when all children have pipeline: source."""
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_EX_BP")
    _seed_all_upstreams(artifacts, wid)

    # First run creates Case A
    asyncio.run(stage.run_one(word_id=wid))

    # Second run should pass preflight (all children are pipeline:)
    result = asyncio.run(stage.run_one(word_id=wid))
    assert result.payload["case"] == "B"


def test_export_case_b_preflight_fail_raises(at_head):
    """Case B preflight fails if a child has human: source under pipeline: parent."""
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_EX_BF")
    _seed_all_upstreams(artifacts, wid)

    # Create Case A first
    result = asyncio.run(stage.run_one(word_id=wid))
    app_wid = result.payload["app_word_id"]

    # Manually insert a human: meaning to simulate conflict
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.meanings (word_id, pos, cn_paraphrase, source) "
                "VALUES (:w, 1, '人工标注', 'human:editor:alice')"
            ),
            {"w": app_wid},
        )

    # Now run export again — preflight should fail
    with pytest.raises(AssertionError, match="preflight.*child row source.*human:"):
        asyncio.run(stage.run_one(word_id=wid))


def test_export_case_c_human_takeover_skips_app_writes(at_head):
    """Case C: existing human: row → skip app.* writes, just update pipeline.words."""
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_EX_C1")
    _seed_all_upstreams(artifacts, wid)

    # Pre-insert a human: word with same form+type
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (type, form, source) VALUES (1, 'apple', 'human:editor:bob')"
            )
        )

    result = asyncio.run(stage.run_one(word_id=wid))
    assert result.payload["case"] == "C"

    # Verify pipeline.words updated
    with engine.begin() as conn:
        status = conn.execute(
            sa.text("SELECT status FROM pipeline.words WHERE id = :w"),
            {"w": wid},
        ).scalar_one()
    assert status == "done"


def test_export_case_c_preflight_fail_raises(at_head):
    """Case C preflight fails if a pipeline: child exists under human: parent."""
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_EX_CF")
    _seed_all_upstreams(artifacts, wid)

    # Pre-insert a human: word
    with engine.begin() as conn:
        human_wid = conn.execute(
            sa.text(
                "INSERT INTO domain.words (type, form, source) "
                "VALUES (1, 'apple', 'human:editor:bob') "
                "RETURNING word_id"
            )
        ).scalar_one()
        # Add a pipeline: child under human: parent
        conn.execute(
            sa.text(
                "INSERT INTO domain.meanings (word_id, pos, cn_paraphrase, source) "
                "VALUES (:w, 1, 'test', 'pipeline:anthropic:claude-opus-4:paraphrase_v1')"
            ),
            {"w": human_wid},
        )

    with pytest.raises(AssertionError, match="preflight.*child row source.*pipeline:"):
        asyncio.run(stage.run_one(word_id=wid))


def test_export_quality_gate_failed_word_raises(at_head):
    """Export refuses to run if quality_gate payload says passed=False."""
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_EX_QGF")
    _seed_all_upstreams(artifacts, wid)

    # Override quality_gate to failed
    artifacts.upsert(
        word_id=wid,
        stage_name="quality_gate",
        fingerprint="fp_qg_fail",
        payload={
            "passed": False,
            "failed_rules": ["test_rule"],
            "checked_at": "2026-04-29T00:00:00+00:00",
        },
        source="pipeline:local:quality_gate_v1",
    )

    with pytest.raises(ValueError, match="quality_gate did not pass"):
        asyncio.run(stage.run_one(word_id=wid))


def test_export_concurrent_modification_error(at_head):
    """UPSERT returning 0 rows raises ConcurrentModificationError."""
    engine = make_engine()
    stage, artifacts = _make_stage(engine)
    wid = _seed_word(engine, batch_id="B_EX_CME")
    _seed_all_upstreams(artifacts, wid)

    # Pre-insert a human: word with same form+type — but NO children
    # This means 0a finds it, case dispatches to C... but let's test the
    # UPSERT path directly. We can simulate by inserting a human: row
    # AFTER the probe — but simpler: modify _upsert_app_words behavior.
    # Actually the cleanest test is: insert an import: row (not pipeline:)
    # which causes UPSERT ON CONFLICT WHERE pipeline: to return 0 rows
    # on the UPDATE branch. But wait — case dispatch will put this as C.
    # The real scenario for ConcurrentModificationError is: probe says no row
    # (Case A), but between probe and INSERT, another process inserts a human:
    # row. With serializable isolation this can't happen, but PG default is
    # read-committed. We simulate by inserting a human: row, then calling
    # _upsert_app_words directly.
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO domain.words (type, form, source) VALUES (1, 'apple', 'human:editor:x')"
            )
        )

    # Call the internal method directly to test the UPSERT 0-rows path
    row_w = {
        "type": 1,
        "form": "apple",
        "phonetic_us": None,
        "phonetic_uk": None,
        "audio_us": None,
        "audio_uk": None,
        "structure": None,
        "plural": None,
        "past_tense": None,
        "past_participle": None,
        "third_person": None,
        "present_participle": None,
        "comparative": None,
        "superlative": None,
        "derivatives": None,
        "source": "pipeline:local:export_v1",
    }
    with (
        pytest.raises(ConcurrentModificationError, match="UPSERT returned 0 rows"),
        engine.begin() as conn,
    ):
        stage._upsert_app_words(conn, row_w)


from wordforge.stages.export import _POS_MAP


def test_pos_map_has_extended_keys():
    """Spec §5.2: _POS_MAP must include num / art / phrasal_verb."""
    assert _POS_MAP["num"] == 9
    assert _POS_MAP["art"] == 10
    assert _POS_MAP["phrasal_verb"] == 201
    assert _POS_MAP["n"] == 1
    assert _POS_MAP["interj"] == 8
