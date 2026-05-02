"""P5a Task 1: FetchDictStage pulls raw Youdao JSON and writes stage_artifacts."""

from __future__ import annotations

import asyncio

import pytest
import sqlalchemy as sa

from wordforge.config import StageConfig
from wordforge.db.engine import make_engine
from wordforge.ingest import ingest_words
from wordforge.pipeline.artifacts import StageArtifactStore
from wordforge.stages.fetch_dict import FetchDictStage


class _FakeYoudao:
    """Stand-in for YoudaoClient — zero network."""

    def __init__(self, payload: dict):
        self._payload = payload
        self.calls: list[str] = []

    def fetch(self, word: str) -> dict:
        self.calls.append(word)
        return self._payload


@pytest.fixture
def stage(at_head):
    engine = make_engine()
    artifacts = StageArtifactStore(engine)
    cfg = StageConfig(parser_version="1", cost_estimate_usd=0.0)
    client = _FakeYoudao({"raw_json": {"simple": {"word": [{"usphone": "ˈæp(ə)l"}]}}})
    stage = FetchDictStage(engine=engine, artifacts=artifacts, config=cfg, client=client)
    return stage, engine, client


def test_fetch_dict_expected_fingerprint_is_deterministic(stage):
    s, _, _ = stage
    fp1 = s.expected_fingerprint(word_id=1)
    fp2 = s.expected_fingerprint(word_id=1)
    assert fp1 == fp2
    assert len(fp1) == 64  # sha256 hex


def test_fetch_dict_run_one_returns_payload(stage):
    s, engine, client = stage
    ids = _seed_words(engine, ["apple"])
    payload = asyncio.run(s.run_one(word_id=ids[0]))
    assert payload.source.startswith("pipeline:youdao")
    assert "simple" in payload.payload["raw_json"]
    assert client.calls == ["apple"]


def test_fetch_dict_payload_has_expected_shape(stage):
    # Lock that payload contains both raw_json and the form (source trace).
    s, engine, _ = stage
    ids = _seed_words(engine, ["apple"])
    payload = asyncio.run(s.run_one(word_id=ids[0]))
    assert "form" in payload.payload
    assert payload.payload["form"] == "apple"


def test_fetch_dict_close_releases_httpx_client(at_head):
    """FetchDictStage.close() disposes httpx client without raising."""
    import gc
    import os

    os.environ["WORDFORGE_STUB_YOUDAO_JSON"] = '{"simple":{"word":[{"usphone":"x"}]}}'
    try:
        from wordforge.config import StageConfig
        from wordforge.db.engine import make_engine
        from wordforge.pipeline.artifacts import StageArtifactStore
        from wordforge.stages.fetch_dict import FetchDictStage

        eng = make_engine()
        cfg = StageConfig(parser_version="1", cost_estimate_usd=0.0)
        s = FetchDictStage(engine=eng, artifacts=StageArtifactStore(eng), config=cfg)
        s.close()  # should not raise
        gc.collect()
        # Basic smoke: close is idempotent.
        s.close()
        eng.dispose()
    finally:
        os.environ.pop("WORDFORGE_STUB_YOUDAO_JSON", None)


def _seed_words(engine, forms: list[str]) -> list[int]:
    ingest_words(engine, raw_forms=forms, batch_id="B_FETCH")
    with engine.begin() as conn:
        rows = conn.execute(
            sa.text("SELECT id FROM pipeline.words WHERE batch_id = 'B_FETCH' ORDER BY id")
        ).all()
    return [r[0] for r in rows]
