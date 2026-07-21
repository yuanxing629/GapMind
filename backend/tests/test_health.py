"""Smoke test - verifies FastAPI app can be constructed and health route works."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_root() -> None:
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "GapMind API"


def test_health() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_health_trailing_slash() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/health/")
    assert resp.status_code == 200
