"""Smoke test: app factory + health endpoint + envelope + request_id header."""
from fastapi.testclient import TestClient

from wordforge.web.app import create_app


def test_health_endpoint():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "ok"
    assert body["error"] is None
    assert "X-Request-ID" in resp.headers


def test_unknown_path_returns_envelope_404():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/nonexistent")
    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"
    assert "X-Request-ID" in resp.headers
