"""共用的 FastAPI dependencies。"""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


# Session factory 在 db.session 中延迟创建，并在此为 deps 重新导出。
# 直接导入会产生循环依赖，因此放在函数内部导入。
def get_db() -> Generator[Session, None, None]:
    """生成 SQLAlchemy session 的 FastAPI dependency。"""
    from app.db.session import SessionLocal

    session_factory: sessionmaker[Session] = SessionLocal
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def get_settings_dep() -> "settings.__class__":  # type: ignore[valid-type]
    """返回缓存 Settings 实例的 FastAPI dependency。"""
    return settings


def authentication_required() -> bool:
    """返回当前部署是否必须认证每个 API 请求。"""
    return settings.auth_required or settings.app_env != "development"


def _token_users() -> dict[str, str]:
    """解析有意保持精简的交付期 token 注册表。"""
    users: dict[str, str] = {}
    for item in settings.auth_tokens.split(","):
        token, separator, user_id = item.strip().partition(":")
        if separator and token and user_id.strip():
            users[token] = user_id.strip()[:128]
    return users


def resolve_user_id(
    *,
    authorization: str | None = None,
    x_user_id: str | None = None,
) -> str:
    """根据 Bearer auth 解析用户，并提供仅限开发环境的回退。

    应用在开发环境之外运行时，刻意不接受 ``X-User-ID`` 作为身份来源。
    token 注册表是轻量的比赛/部署保护，不用于替代机构 IdP。
    """
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.casefold() != "bearer" or not token.strip():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_authorization",
                    "message": "Use a Bearer token",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        user_id = _token_users().get(token.strip())
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_token",
                    "message": "Bearer token is invalid or expired",
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user_id

    if authentication_required():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "authentication_required",
                "message": "A valid Bearer token is required",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return (x_user_id or "user").strip()[:128] or "user"


def get_current_user(
    request: Request,
    x_user_id: str | None = Header(default=None, alias="X-User-ID"),
    authorization: str | None = Header(default=None, alias="Authorization"),
    db: Session = Depends(get_db),
) -> str:
    """为路由 dependency 解析实际操作者身份。"""
    state_user_id = getattr(request.state, "user_id", None)
    if state_user_id:
        return state_user_id
    raw_session = request.cookies.get(settings.auth_cookie_name)
    if raw_session:
        from app.domains.auth.service import AuthService

        resolved = AuthService(db).resolve_session(raw_session)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "error": "invalid_session",
                    "message": "登录状态已失效，请重新登录",
                    "retryable": False,
                },
                headers={"WWW-Authenticate": "Session"},
            )
        user_id, session_id = resolved
        request.state.user_id = user_id
        request.state.session_id = session_id
        return user_id
    return resolve_user_id(authorization=authorization, x_user_id=x_user_id)


def get_owned_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """仅在 Workspace 属于实际操作者时解析它。

    URL 统一为 ``/workspaces/{workspace_id}/...`` 的路由可以使用该依赖作为规范所有权检查。
    交付中间件在路由前能够解析 session 时会重复同一检查，而该依赖可以约束本地开发和直接路由调用方。
    """
    from app.domains.workspace.service import WorkspaceService

    return WorkspaceService(db).get(workspace_id, actor_id=user_id)
