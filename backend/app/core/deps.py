"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


# Session factory is created lazily in db.session; re-exported here for deps.
# Importing here would create a circular import, so we import inside the function.
def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy session."""
    from app.db.session import SessionLocal

    session_factory: sessionmaker[Session] = SessionLocal
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def get_settings_dep() -> "settings.__class__":  # type: ignore[valid-type]
    """FastAPI dependency returning the cached Settings instance."""
    return settings


def authentication_required() -> bool:
    """Return whether this deployment must authenticate every API request."""
    return settings.auth_required or settings.app_env != "development"


def _token_users() -> dict[str, str]:
    """Parse the intentionally small delivery-time token registry."""
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
    """Resolve a user from Bearer auth, with a development-only fallback.

    ``X-User-ID`` is deliberately not accepted as an identity source once the
    app is running outside development.  The token registry is a minimal
    competition/deployment guard, not a replacement for an institutional IdP.
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
    """Resolve the acting user identity for a route dependency."""
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
    """Resolve a Workspace only when it belongs to the acting user.

    Routers whose URL is uniformly ``/workspaces/{workspace_id}/...`` can use
    this dependency as the canonical ownership check. The delivery middleware
    repeats the same check when it can resolve a session before routing, while
    this dependency keeps local development and direct route callers honest.
    """
    from app.domains.workspace.service import WorkspaceService

    return WorkspaceService(db).get(workspace_id, actor_id=user_id)
