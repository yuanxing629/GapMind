"""Health check endpoints.

``/health`` is a cheap liveness probe. ``/health/ready`` reports the
dependencies that are required by the local research workspace and returns a
non-2xx response when a required dependency is unavailable. Provider checks
are deliberately split into configuration checks and network checks so a key
being present is never presented as proof that an external service is healthy.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from redis import Redis
from sqlalchemy import text

from app.core.config import settings
from app.core.deps import authentication_required
from app.gateway.embedding import get_embedding_gateway
from app.gateway.llm import get_llm_gateway
from app.gateway.reranker import get_reranker_gateway

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
@router.get("/")
def health() -> dict[str, str]:
    """Liveness check - always 200 if the process is up."""
    return {"status": "ok", "env": settings.app_env}


def _ok(*, detail: str = "ok", checked: str = "network") -> dict[str, str]:
    return {"status": "ok", "detail": detail, "checked": checked}


def _missing(*, detail: str, checked: str = "configuration") -> dict[str, str]:
    return {"status": "missing", "detail": detail, "checked": checked}


def _error(*, detail: str, checked: str = "network") -> dict[str, str]:
    return {"status": "error", "detail": detail, "checked": checked}


def _check_database() -> dict[str, str]:
    """Run a bounded SQL probe without exposing connection details."""
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return _ok()
    except Exception:
        return _error(detail="database_unavailable")


def _check_redis() -> dict[str, str]:
    client: Redis | None = None
    try:
        client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=0.8,
            socket_timeout=0.8,
            health_check_interval=30,
        )
        if not client.ping():
            return _error(detail="redis_ping_failed")
        return _ok()
    except Exception:
        return _error(detail="redis_unavailable")
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _check_milvus() -> dict[str, str]:
    """Check Milvus connectivity without creating or loading a collection."""
    try:
        from app.domains.retrieval import milvus_client

        client = milvus_client.get_milvus_client()
        client.list_collections()
        return _ok()
    except Exception:
        return _error(detail="milvus_unavailable")


def _check_storage() -> dict[str, str]:
    path = Path(settings.app_storage_dir).expanduser()
    try:
        if not path.exists() or not path.is_dir():
            return _error(detail="storage_directory_missing", checked="filesystem")
        if not os.access(path, os.W_OK):
            return _error(detail="storage_not_writable", checked="filesystem")
        return _ok(checked="filesystem")
    except OSError:
        return _error(detail="storage_unavailable", checked="filesystem")


def _check_worker() -> dict[str, str]:
    """Check that at least one Celery worker answers the control ping."""
    try:
        from app.workers.celery_app import celery_app

        inspector = celery_app.control.inspect(timeout=0.8)
        replies = inspector.ping() or {}
        if replies:
            return _ok(detail="worker_replied")
        return _error(detail="celery_worker_unavailable")
    except Exception:
        return _error(detail="celery_worker_unavailable")


def _check_configured(checker: Callable[[], bool], *, missing: str) -> dict[str, str]:
    try:
        return _ok(detail="configured", checked="configuration") if checker() else _missing(detail=missing)
    except Exception:
        return _error(detail="configuration_check_failed", checked="configuration")


def _readiness_checks() -> dict[str, dict[str, str]]:
    llm = get_llm_gateway()
    embedding = get_embedding_gateway()
    reranker = get_reranker_gateway()
    return {
        "database": _check_database(),
        "redis": _check_redis(),
        "milvus": _check_milvus(),
        "storage": _check_storage(),
        "celery_worker": _check_worker(),
        "llm": _check_configured(llm.ping, missing="llm_api_key_missing"),
        "embedding": _check_configured(embedding.ping, missing="embedding_api_key_missing"),
        "reranker": _check_configured(reranker.ping, missing="reranker_api_key_missing"),
        "semantic_scholar": _check_configured(
            lambda: bool(settings.semantic_scholar_base_url),
            missing="semantic_scholar_base_url_missing",
        ),
        "auth": _check_configured(
            lambda: (not authentication_required())
            or settings.auth_is_configured
            or bool(settings.auth_tokens.strip()),
            missing="auth_session_secret_missing",
        ),
    }


@router.get("/ready")
def readiness() -> JSONResponse:
    """Return dependency readiness with a truthful HTTP status.

    Database, Redis, Milvus, storage, LLM and Embedding are required for the
    core workspace path. A Celery worker is required for asynchronous
    extraction/agent paths. Reranker and Semantic Scholar are reported but do
    not make the API entirely unavailable because the product has explicit
    degraded/partial-success paths for them.
    """
    checks = _readiness_checks()
    required = ("database", "redis", "milvus", "storage", "llm", "embedding", "auth")
    # ``checks`` is kept injectable for unit tests and deployment probes; a
    # legacy probe that does not know the newer auth check should not crash
    # the endpoint. The production implementation always includes ``auth``.
    required_failures = [
        name for name in required if name in checks and checks[name]["status"] != "ok"
    ]
    degraded = [
        name
        for name in ("celery_worker", "reranker", "semantic_scholar")
        if checks[name]["status"] != "ok"
    ]
    if required_failures:
        overall = "not_ready"
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    elif degraded:
        overall = "degraded"
        http_status = status.HTTP_200_OK
    else:
        overall = "ok"
        http_status = status.HTTP_200_OK
    payload: dict[str, Any] = {
        "status": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "required_failures": required_failures,
        "degraded": degraded,
    }
    return JSONResponse(status_code=http_status, content=payload)
