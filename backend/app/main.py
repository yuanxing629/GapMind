"""FastAPI 应用入口。

Phase 0：提供 health check、CORS 和 logging 的 app 骨架。Domain router 在 Phase 1+ 接入。
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.deps import authentication_required, resolve_user_id
from app.core.exception_handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal  # noqa: F401  (ensures engine is created at import)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings.validate_runtime_security()
    configure_logging()
    logger.info(
        "app.startup",
        env=settings.app_env,
        host=settings.app_host,
        port=settings.app_port,
    )
    yield
    logger.info("app.shutdown")


app = FastAPI(
    title="GapMind API",
    description="Evidence-grounded, Human-in-the-Loop AI Research Workspace",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-User-ID", "X-CSRF-Token"],
    expose_headers=["X-File-Name"],
)


_PUBLIC_PATHS = {
    "/api/v1/auth/login",
    "/api/v1/auth/invites/validate",
    "/api/v1/auth/invites/accept",
    "/api/v1/auth/forgot-password",
    "/api/v1/auth/reset-password",
}


@app.middleware("http")
async def enforce_delivery_access(request: Request, call_next):
    """在开发环境之外要求交付认证和工作区所有权。

    这是为比赛交付包设置的轻量部署保护，不用于替代机构身份提供商、
    群组成员服务或完整的 RBAC 实现。
    """
    path = request.url.path
    if (
        not path.startswith("/api/v1")
        or path.startswith("/api/v1/health")
        or path in _PUBLIC_PATHS
    ):
        return await call_next(request)

    user_id: str | None = None
    session_id: str | None = None
    raw_session = request.cookies.get(settings.auth_cookie_name)
    if raw_session and request.method in {"POST", "PATCH", "DELETE"}:
        csrf_cookie = request.cookies.get(settings.auth_csrf_cookie_name)
        csrf_header = request.headers.get("X-CSRF-Token")
        if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": {
                        "error": "csrf_failed",
                        "message": "请求缺少有效的 CSRF 校验令牌",
                        "retryable": False,
                    }
                },
            )
    if raw_session and not settings.is_dev:
        from app.domains.auth.service import AuthService

        try:
            with SessionLocal() as db:
                resolved = AuthService(db).resolve_session(raw_session)
        except Exception:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": {
                        "error": "auth_unavailable",
                        "message": "登录状态校验暂时不可用",
                        "retryable": True,
                    }
                },
            )
        if resolved is None:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": {
                        "error": "invalid_session",
                        "message": "登录状态已失效，请重新登录",
                        "retryable": False,
                    }
                },
                headers={"WWW-Authenticate": "Session"},
            )
        user_id, session_id = resolved
    elif raw_session and settings.is_dev:
# 开发测试和本地客户端通过普通 FastAPI dependency 解析 cookie，
# 该 dependency 可以使用注入的测试 session。不要在这里替换为历史匿名回退。
        user_id = None
    else:
        try:
            user_id = resolve_user_id(
                authorization=request.headers.get("Authorization"),
                x_user_id=request.headers.get("X-User-ID"),
            )
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )

    if authentication_required() and user_id is not None:
        parts = path.removeprefix("/api/v1/").split("/")
        if len(parts) >= 2 and parts[0] == "workspaces":
            workspace_id = parts[1]
            if workspace_id != "independent":
                from app.domains.workspace.access import has_workspace_access

                try:
                    with SessionLocal() as db:
                        allowed = has_workspace_access(db, workspace_id, user_id)
                except Exception:
                    return JSONResponse(
                        status_code=503,
                        content={
                            "detail": {
                                "error": "workspace_acl_unavailable",
                                "message": "Workspace access check is temporarily unavailable",
                                "retryable": True,
                            }
                        },
                    )
                if not allowed:
                    return JSONResponse(
                        status_code=404,
                        content={
                            "detail": {
                                "error": "workspace_not_found",
                                "message": "Workspace not found",
                                "retryable": False,
                            }
                        },
                    )
# Workspace 访问仅限所有者。当前没有共享成员角色，因此通过上面的存在性/所有权
# 检查后，所有者可以执行全部操作。

    if user_id is not None:
        request.state.user_id = user_id
    if session_id is not None:
        request.state.session_id = session_id
    return await call_next(request)

app.include_router(api_router)


@app.get("/")
def root() -> dict[str, str]:
    """根路径重定向提示，实际 API 位于 /api/v1 下。"""
    return {"name": "GapMind API", "docs": "/docs", "openapi": "/openapi.json"}
