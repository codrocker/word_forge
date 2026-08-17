"""Smoke test: app factory + health endpoint + envelope + request_id header."""
from pathlib import Path

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


def test_spa_frontend_route_serves_index_html():
    """Non-API paths (e.g. /login, /words/123) serve SPA index.html."""
    # Resolve dist_dir same way as app.py
    dist_dir = (
        Path(__file__).resolve().parent.parent.parent
        / "frontend"
        / "dist"
    )
    app = create_app()
    client = TestClient(app)
    if dist_dir.is_dir() and (dist_dir / "index.html").exists():
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<!doctype html" in resp.text.lower()

        resp2 = client.get("/words/123")
        assert resp2.status_code == 200
        assert "text/html" in resp2.headers["content-type"]
    else:
        # dist not present — SPA mount skipped, non-API paths get 404
        resp = client.get("/login")
        assert resp.status_code == 404
