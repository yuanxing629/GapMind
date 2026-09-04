"""Smoke 测试：验证 FastAPI 应用可构造且 health 路由可用。"""

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


def _ready_checks(*, database: str = "ok") -> dict[str, dict[str, str]]:
    return {
        "database": {"status": database, "detail": "ok", "checked": "network"},
        "redis": {"status": "ok", "detail": "ok", "checked": "network"},
        "milvus": {"status": "ok", "detail": "ok", "checked": "network"},
        "storage": {"status": "ok", "detail": "ok", "checked": "filesystem"},
        "celery_worker": {"status": "ok", "detail": "worker_replied", "checked": "network"},
        "llm": {"status": "ok", "detail": "configured", "checked": "configuration"},
        "embedding": {"status": "ok", "detail": "configured", "checked": "configuration"},
        "reranker": {"status": "ok", "detail": "configured", "checked": "configuration"},
        "semantic_scholar": {"status": "ok", "detail": "configured", "checked": "configuration"},
    }


def test_readiness_returns_503_when_required_dependency_is_unavailable(monkeypatch) -> None:
    from app.api.v1 import health as health_module

    monkeypatch.setattr(
        health_module,
        "_readiness_checks",
        lambda: _ready_checks(database="error"),
    )
    client = TestClient(app)
    resp = client.get("/api/v1/health/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["required_failures"] == ["database"]


def test_readiness_allows_optional_degraded_dependencies(monkeypatch) -> None:
    from app.api.v1 import health as health_module

    checks = _ready_checks()
    checks["celery_worker"] = {
        "status": "error",
        "detail": "celery_worker_unavailable",
        "checked": "network",
    }
    monkeypatch.setattr(health_module, "_readiness_checks", lambda: checks)
    client = TestClient(app)
    resp = client.get("/api/v1/health/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
    assert body["degraded"] == ["celery_worker"]
