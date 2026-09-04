"""健康检查端点。

``/health`` 是轻量存活探针。``/health/ready`` 报告本地科研工作区所需的依赖，
当必要依赖不可用时返回非 2xx 响应。提供商检查刻意拆分为配置检查和网络检查，
因此不会把存在密钥误认为外部服务健康的证据。
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
    """存活检查：进程运行时始终返回 200。"""
    return {"status": "ok", "env": settings.app_env}


def _ok(*, detail: str = "ok", checked: str = "network") -> dict[str, str]:
    return {"status": "ok", "detail": detail, "checked": checked}


def _missing(*, detail: str, checked: str = "configuration") -> dict[str, str]:
    return {"status": "missing", "detail": detail, "checked": checked}


def _error(*, detail: str, checked: str = "network") -> dict[str, str]:
    return {"status": "error", "detail": detail, "checked": checked}


def _check_database() -> dict[str, str]:
    """执行有界 SQL 探测，不暴露连接细节。"""
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
    """检查 Milvus 连通性，不创建或加载 collection。"""
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
    """检查至少有一个 Celery worker 响应 control ping。"""
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
        "llm": _check_configured(llm.ping, missing="llm_config_missing"),
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
    """返回依赖就绪状态，并使用真实反映状态的 HTTP 状态码。

    Database、Redis、Milvus、storage、LLM 和 Embedding 是核心工作区路径的必要依赖。
    异步抽取/Agent 路径需要 Celery worker。Reranker 和 Semantic Scholar 会被报告，
    但不会使 API 完全不可用，因为产品为它们提供了明确的 degraded/partial-success 路径。
    """
    checks = _readiness_checks()
    required = ("database", "redis", "milvus", "storage", "llm", "embedding", "auth")
    # ``checks`` 保持可注入，以供单元测试和部署探针使用；不了解新版 auth 检查的
    # legacy 探针不应导致端点崩溃。生产实现始终包含 ``auth``。
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
