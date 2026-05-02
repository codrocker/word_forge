"""StageArtifactStore: UPSERT / get / should_skip on pipeline.stage_artifacts."""

from __future__ import annotations

import sqlalchemy as sa

from wordforge.pipeline.artifacts import StageArtifactStore


def _seed_word(engine: sa.engine.Engine, word_id: int, form: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO pipeline.words (id, raw_form, normalized_form, type) "
                "VALUES (:id, :f, :f, 1)"
            ),
            {"id": word_id, "f": form},
        )


def test_get_returns_none_when_missing(at_head):
    store = StageArtifactStore(at_head)
    _seed_word(at_head, 1, "apple")
    assert store.get(word_id=1, stage_name="paraphrase") is None


def test_upsert_then_get_roundtrip(at_head):
    store = StageArtifactStore(at_head)
    _seed_word(at_head, 1, "apple")
    store.upsert(
        word_id=1,
        stage_name="paraphrase",
        fingerprint="fp-abc",
        payload={"meanings": [{"cn": "苹果"}]},
        source="pipeline:claude-opus:paraphrase_v2",
        model="claude-opus-4",
        prompt_version="v2",
    )
    row = store.get(word_id=1, stage_name="paraphrase")
    assert row is not None
    assert row["fingerprint"] == "fp-abc"
    assert row["payload"] == {"meanings": [{"cn": "苹果"}]}
    assert row["source"] == "pipeline:claude-opus:paraphrase_v2"
    assert row["model"] == "claude-opus-4"
    assert row["prompt_version"] == "v2"


def test_upsert_overwrites_same_word_stage(at_head):
    store = StageArtifactStore(at_head)
    _seed_word(at_head, 1, "apple")
    store.upsert(
        word_id=1,
        stage_name="paraphrase",
        fingerprint="old",
        payload={"v": 1},
        source="pipeline:x:y",
    )
    store.upsert(
        word_id=1,
        stage_name="paraphrase",
        fingerprint="new",
        payload={"v": 2},
        source="pipeline:x:y",
    )
    row = store.get(word_id=1, stage_name="paraphrase")
    assert row["fingerprint"] == "new"
    assert row["payload"] == {"v": 2}


def test_should_skip_true_on_fingerprint_match(at_head):
    store = StageArtifactStore(at_head)
    _seed_word(at_head, 1, "apple")
    store.upsert(
        word_id=1,
        stage_name="paraphrase",
        fingerprint="fp-X",
        payload={},
        source="pipeline:x:y",
    )
    assert (
        store.should_skip(word_id=1, stage_name="paraphrase", expected_fingerprint="fp-X") is True
    )


def test_should_skip_false_on_fingerprint_mismatch(at_head):
    store = StageArtifactStore(at_head)
    _seed_word(at_head, 1, "apple")
    store.upsert(
        word_id=1,
        stage_name="paraphrase",
        fingerprint="fp-X",
        payload={},
        source="pipeline:x:y",
    )
    assert (
        store.should_skip(word_id=1, stage_name="paraphrase", expected_fingerprint="fp-Y") is False
    )


def test_should_skip_false_when_missing(at_head):
    store = StageArtifactStore(at_head)
    _seed_word(at_head, 1, "apple")
    assert (
        store.should_skip(word_id=1, stage_name="paraphrase", expected_fingerprint="anything")
        is False
    )
