"""Experiments API (web M8): auth gate, provider listing, run lifecycle
with a stubbed completer, schema-gate recording, and URL guard.

DB setup/teardown uses SQLAlchemy Core table expressions (no SQL strings).
"""
from __future__ import annotations

import time

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from tests.web.conftest import TEST_PASSWORD
from wordforge.db.engine import make_engine
from wordforge.llm.client import LLMClient, LLMCompletion
from wordforge.web.app import create_app
from wordforge.web.services import experiment_service
from wordforge.web.services.editor_service import create_editor

_EMAIL = "test-experiments@wordforge.dev"

_BATCHES = sa.table(
    "batches",
    sa.column("id", sa.Text),
    schema="pipeline",
)
_WORDS = sa.table(
    "words",
    sa.column("id", sa.BigInteger),
    sa.column("raw_form", sa.Text),
    sa.column("normalized_form", sa.Text),
    sa.column("type", sa.SmallInteger),
    sa.column("batch_id", sa.Text),
    schema="pipeline",
)
_ARTIFACTS = sa.table(
    "stage_artifacts",
    sa.column("word_id", sa.BigInteger),
    sa.column("stage_name", sa.Text),
    sa.column("fingerprint", sa.Text),
    sa.column("payload", postgresql.JSONB),
    sa.column("source", sa.Text),
    schema="pipeline",
)
_RUNS = sa.table(
    "experiment_runs", sa.column("editor_id", sa.BigInteger), schema="meta"
)
_SESSIONS = sa.table(
    "editor_sessions", sa.column("editor_id", sa.BigInteger), schema="meta"
)
_EDITORS = sa.table(
    "editors",
    sa.column("id", sa.BigInteger),
    sa.column("email", sa.Text),
    schema="meta",
)


def _editor_ids(conn) -> list[int]:
    rows = conn.execute(sa.select(_EDITORS.c.id).where(_EDITORS.c.email == _EMAIL)).all()
    return [r[0] for r in rows]


def _login_client() -> TestClient:
    create_editor(make_engine(), _EMAIL, "EXP", TEST_PASSWORD)
    c = TestClient(create_app())
    r = c.post("/api/v1/auth/login", json={"email": _EMAIL, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return c


@pytest.fixture(scope="module", autouse=True)
def _purge_stale_state():
    """Shared test DB: clear leftovers from earlier interrupted runs once,
    before any test in this module creates the editor / seeds words."""
    _cleanup()


def _cleanup() -> None:
    e = make_engine()
    with e.begin() as conn:
        ids = _editor_ids(conn)
        if ids:
            conn.execute(_RUNS.delete().where(_RUNS.c.editor_id.in_(ids)))
            conn.execute(_SESSIONS.delete().where(_SESSIONS.c.editor_id.in_(ids)))
        conn.execute(_EDITORS.delete().where(_EDITORS.c.email == _EMAIL))
        word_ids = [
            r[0]
            for r in conn.execute(
                sa.select(_WORDS.c.id).where(_WORDS.c.batch_id == "TEST-EXP")
            ).all()
        ]
        if word_ids:
            conn.execute(_ARTIFACTS.delete().where(_ARTIFACTS.c.word_id.in_(word_ids)))
            conn.execute(_WORDS.delete().where(_WORDS.c.id.in_(word_ids)))
        conn.execute(_BATCHES.delete().where(_BATCHES.c.id == "TEST-EXP"))
    e.dispose()


@pytest.fixture()
def seeded_words():
    e = make_engine()
    with e.begin() as conn:
        conn.execute(_BATCHES.insert().values(id="TEST-EXP"))
        for form in ("apple", "banana", "cherry"):
            wid = conn.execute(
                _WORDS.insert()
                .values(raw_form=form, normalized_form=form, type=1, batch_id="TEST-EXP")
                .returning(_WORDS.c.id)
            ).scalar_one()
            conn.execute(
                _ARTIFACTS.insert().values(
                    word_id=wid,
                    stage_name="fetch_dict",
                    fingerprint="fp-test",
                    payload={"raw_json": {}},
                    source="test",
                )
            )
    yield ["apple", "banana", "cherry"]
    _cleanup()


class _NullStore:
    """CacheStore stand-in: never hits, records nothing."""

    def get(self, kind, key):
        return None

    def put(self, **kwargs):
        return None


def _stub_llm(responder):
    def _factory(engine):
        return LLMClient(store=_NullStore(), completers={"openai": responder})

    return _factory


def _good_responder(*, model: str, prompt: str, **params):
    assert "{word}" not in prompt, "prompt must be rendered"
    return LLMCompletion(
        response={"text": '{"meanings": [{"pos": "n", "cn": "测试"}]}', "in_tok": 3, "out_tok": 5},
        cost_usd=0.0007,
    )


def _poll_done(client: TestClient, run_id: int, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/experiments/runs/{run_id}").json()["data"]["run"]
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.1)
    pytest.fail(f"run {run_id} never finished")


def test_experiments_require_auth():
    c = TestClient(create_app())
    assert c.get("/api/v1/experiments/providers").status_code == 401
    assert c.get("/api/v1/experiments/runs").status_code == 401


def test_providers_lists_registry(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    c = _login_client()
    try:
        r = c.get("/api/v1/experiments/providers")
        assert r.status_code == 200
        data = r.json()["data"]
        ids = [p["id"] for p in data["providers"]]
        assert "openai" in ids
        openai_entry = next(p for p in data["providers"] if p["id"] == "openai")
        assert openai_entry["api_key_env"] == "OPENAI_API_KEY"
        assert openai_entry["available"] is True
        assert any(s["stage"] == "paraphrase" for s in data["stages"])
    finally:
        _cleanup()


def test_run_lifecycle_with_stub(monkeypatch, seeded_words):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    monkeypatch.setattr(experiment_service, "_new_llm", _stub_llm(_good_responder))
    c = _login_client()
    try:
        r = c.post(
            "/api/v1/experiments/runs",
            json={"provider": "openai", "model": "stub-model", "stage": "paraphrase",
                  "word_count": 3, "seed": 7},
        )
        assert r.status_code == 202, r.text
        run_id = r.json()["data"]["run_id"]

        run = _poll_done(c, run_id)
        assert run["status"] == "done", run.get("error")
        assert run["ok_count"] == 3
        assert run["valid_count"] == 3
        assert run["total_cost_usd"] == pytest.approx(0.0021)
        assert sorted(x["word"] for x in run["results"]) == sorted(seeded_words)
        assert all(x["valid"] for x in run["results"])

        listing = c.get("/api/v1/experiments/runs").json()["data"]["items"]
        assert any(item["id"] == run_id for item in listing)
    finally:
        _cleanup()


def test_run_records_schema_failure(monkeypatch, seeded_words):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")

    def _bad_responder(*, model: str, prompt: str, **params):
        return LLMCompletion(response={"text": "definitely not json"}, cost_usd=0.0001)

    monkeypatch.setattr(experiment_service, "_new_llm", _stub_llm(_bad_responder))
    c = _login_client()
    try:
        r = c.post(
            "/api/v1/experiments/runs",
            json={"provider": "openai", "model": "stub-model", "stage": "paraphrase",
                  "word_count": 2, "seed": 7},
        )
        run = _poll_done(c, r.json()["data"]["run_id"])
        assert run["status"] == "done"
        assert run["ok_count"] == 0
        assert run["valid_count"] == 0
        assert all(x["ok"] is False and x["error"] for x in run["results"])
    finally:
        _cleanup()


def test_run_rejects_bad_stage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unit-test-placeholder")
    c = _login_client()
    try:
        r = c.post(
            "/api/v1/experiments/runs",
            json={"provider": "openai", "model": "m", "stage": "nope",
                  "word_count": 1, "seed": 1},
        )
        assert r.status_code == 400
        assert "not experimentable" in r.json()["error"]["message"]
    finally:
        _cleanup()


def test_run_404_for_missing_id():
    c = _login_client()
    try:
        assert c.get("/api/v1/experiments/runs/99999999").status_code == 404
    finally:
        _cleanup()


def test_model_listing_url_guard():
    with pytest.raises(experiment_service.ExperimentError):
        experiment_service._assert_public_http_url("http://localhost:8000/v1/models")
    with pytest.raises(experiment_service.ExperimentError):
        experiment_service._assert_public_http_url("https://10.0.0.5/v1/models")
    with pytest.raises(experiment_service.ExperimentError):
        experiment_service._assert_public_http_url("ftp://api.example.com/v1")
    experiment_service._assert_public_http_url("https://api.deepseek.com/v1/models")
    with pytest.raises(experiment_service.ExperimentError):
        experiment_service.fetch_models("no-such-provider")
