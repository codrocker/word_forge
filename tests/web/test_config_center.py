"""Config center (M9) tests: versioning, rollback, encrypted-at-rest
keys that never appear in responses, agent lifecycle, and agent-run
experiments with a stubbed completer.

No direct SQL here: names are session-unique (leftover rows are wiped by
the migration tests' periodic `alembic downgrade base`), word seeding
reuses the committed test_experiments fixture, and at-rest checks go
through config_center_service.stored_secret.
"""
from __future__ import annotations

import secrets
import time
import uuid

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from tests.web.conftest import TEST_PASSWORD
from wordforge.db.engine import make_engine
from wordforge.llm.client import LLMClient, LLMCompletion
from wordforge.web import secrets_box
from wordforge.web.app import create_app
from wordforge.web.services import agent_run_service, config_center_service
from wordforge.web.services.editor_service import create_editor

_UNIQ = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"


def _new_key_material() -> str:
    # Runtime-generated opaque test value; not a real credential shape.
    return "test-" + secrets.token_hex(16)


@pytest.fixture(autouse=True)
def _secret_env(monkeypatch):
    monkeypatch.setenv("WORDFORGE_CONFIG_SECRET", Fernet.generate_key().decode())


def _login_client() -> TestClient:
    # Per-test unique email: no cross-test unique-key collisions, and
    # leftover rows are wiped by the migration tests' downgrade cycles.
    email = f"cc-{_UNIQ}-{secrets.token_hex(3)}@wordforge.dev"
    create_editor(make_engine(), email, "CC", TEST_PASSWORD)
    c = TestClient(create_app())
    r = c.post("/api/v1/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return c


def _mk_provider(c: TestClient) -> dict:
    material = _new_key_material()
    r = c.post(
        "/api/v1/config-center/providers",
        json={
            "name": f"relay-{_UNIQ}-{secrets.token_hex(3)}",
            "transport": "openai",
            "base_url": "https://relay.example.test/v1",
            "api_key": material,
            "notes": "unit test fixture",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()["data"]["provider"]
    assert body["api_key_last4"] == material[-4:]
    return body


def _mk_prompt(c: TestClient) -> dict:
    r = c.post(
        "/api/v1/config-center/prompts",
        json={
            "name": f"p-{_UNIQ}-{secrets.token_hex(3)}",
            "stage": "paraphrase",
            "content": "Word: {word}\nDict: {dict_summary}\nReturn JSON meanings.",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]["prompt"]


def test_config_center_requires_auth():
    c = TestClient(create_app())
    assert c.get("/api/v1/config-center/providers").status_code == 401
    assert c.get("/api/v1/config-center/agents").status_code == 401


def test_provider_lifecycle_and_key_safety():
    c = _login_client()
    p = _mk_provider(c)
    last4 = p["api_key_last4"]

    # key material never serialized in any response
    dumped = str(c.get(f"/api/v1/config-center/providers/{p['id']}").json())
    assert dumped.count(last4) == 1  # only the masked last-4 mention
    assert p["has_key"] is True and p["current_version"] == 1

    # encrypted at rest: stored value decrypts back; ciphertext != plaintext
    e = make_engine()
    stored = config_center_service.stored_secret(e, p["id"])
    e.dispose()
    assert stored is not None
    decrypted = secrets_box.decrypt_key(stored)
    assert decrypted.endswith(last4) and len(decrypted) > len(last4)
    assert decrypted != stored

    # edit -> v2 (non-secret fields only; key untouched when not provided)
    r = c.patch(
        f"/api/v1/config-center/providers/{p['id']}",
        json={"base_url": "https://relay2.example.test/v1"},
    )
    assert r.status_code == 200
    p2 = r.json()["data"]["provider"]
    assert p2["current_version"] == 2
    assert p2["base_url"] == "https://relay2.example.test/v1"
    assert p2["api_key_last4"] == last4
    assert len(p2["versions"]) == 2

    # rollback -> v1 active, history intact
    r = c.post(f"/api/v1/config-center/providers/{p['id']}/rollback", json={"version": 1})
    assert r.status_code == 200
    assert r.json()["data"]["provider"]["current_version"] == 1


def test_provider_validation_errors():
    c = _login_client()
    r = c.post(
        "/api/v1/config-center/providers",
        json={"name": f"bad-{_UNIQ}", "transport": "openai",
              "base_url": "http://localhost:8000/v1", "api_key": _new_key_material()},
    )
    assert r.status_code == 400


def test_prompt_lifecycle_and_rollback():
    c = _login_client()
    pr = _mk_prompt(c)
    assert pr["current_version"] == 1

    r = c.patch(
        f"/api/v1/config-center/prompts/{pr['id']}",
        json={"content": "v2 template {word} {dict_summary}"},
    )
    assert r.status_code == 200
    pr2 = r.json()["data"]["prompt"]
    assert pr2["current_version"] == 2
    assert pr2["versions"][0]["content"].startswith("v2 template")

    r = c.post(f"/api/v1/config-center/prompts/{pr['id']}/rollback", json={"version": 1})
    assert r.status_code == 200
    assert r.json()["data"]["prompt"]["current_version"] == 1


def test_agent_lifecycle_pinning_and_rollback():
    c = _login_client()
    p = _mk_provider(c)
    pr = _mk_prompt(c)

    r = c.post(
        "/api/v1/config-center/agents",
        json={
            "name": f"a1-{_UNIQ}",
            "provider_config_id": p["id"],
            "model": "test-model-x",
            "prompt_id": pr["id"],
        },
    )
    assert r.status_code == 201, r.text
    a = r.json()["data"]["agent"]
    v1 = a["versions"][0]
    assert v1["provider_config_version"] == 1 and v1["prompt_version"] == 1

    r = c.patch(
        f"/api/v1/config-center/agents/{a['id']}",
        json={
            "provider_config_id": p["id"],
            "model": "test-model-y",
            "prompt_id": pr["id"],
        },
    )
    assert r.status_code == 200
    a2 = r.json()["data"]["agent"]
    assert a2["current_version"] == 2
    assert a2["versions"][0]["model"] == "test-model-y"

    r = c.post(f"/api/v1/config-center/agents/{a['id']}/rollback", json={"version": 1})
    assert r.status_code == 200
    assert r.json()["data"]["agent"]["current_version"] == 1

    r = c.post(
        "/api/v1/config-center/agents",
        json={"name": f"bad-{_UNIQ}", "provider_config_id": p["id"],
              "model": "m", "prompt_id": 99999},
    )
    assert r.status_code == 400


class _NullStore:
    def get(self, kind, key):
        return None

    def put(self, **kwargs):
        return None


def test_agent_experiment_run_end_to_end_stubbed(seeded_words):  # noqa: F841
    c = _login_client()
    p = _mk_provider(c)
    pr = _mk_prompt(c)

    r = c.post(
        "/api/v1/config-center/agents",
        json={"name": f"runner-{_UNIQ}", "provider_config_id": p["id"],
              "model": "stub-model", "prompt_id": pr["id"]},
    )
    agent_id = r.json()["data"]["agent"]["id"]

    def _responder(*, model: str, prompt: str, **params):
        assert "{word}" not in prompt
        return LLMCompletion(
            response={"text": '{"meanings": [{"pos": "n", "cn": "x"}]}'},
            cost_usd=0.0005,
        )

    def _factory(engine, resolved):
        return LLMClient(store=_NullStore(), completers={"agent": _responder})

    original = agent_run_service._new_llm_from_recipe
    agent_run_service._new_llm_from_recipe = _factory
    try:
        r = c.post(
            "/api/v1/experiments/runs",
            json={"agent_id": agent_id, "word_count": 3, "seed": 7},
        )
        assert r.status_code == 202, r.text
        run_id = r.json()["data"]["run_id"]

        run = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            run = c.get(f"/api/v1/experiments/runs/{run_id}").json()["data"]["run"]
            if run["status"] in ("done", "error"):
                break
            time.sleep(0.1)
        assert run is not None and run["status"] == "done", run
        assert run["provider"] == "agent"
        assert run["ok_count"] == 3 and run["valid_count"] == 3
        snap = run["resolved_snapshot"]
        assert snap["agent"]["name"].startswith("runner-")
        assert snap["provider_config"]["name"].startswith("relay-")
        assert snap["prompt"]["name"].startswith("p-")
        assert len(snap["prompt"]["sha256"]) == 16
        assert run["agent_version_id"] is not None
        # no secret material anywhere in the run payload
        assert snap["provider_config"].get("api_key") is None
    finally:
        agent_run_service._new_llm_from_recipe = original


def test_agent_run_unknown_agent_400():
    c = _login_client()
    r = c.post(
        "/api/v1/experiments/runs",
        json={"agent_id": 999999, "word_count": 1, "seed": 1},
    )
    assert r.status_code == 400
