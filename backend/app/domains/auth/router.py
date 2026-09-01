"""HTTP endpoints for invitation-based authentication."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_db
from app.domains.auth.models import User, UserInvite
from app.domains.auth.schemas import (
    AuditEventRead,
    AuthUserRead,
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    InviteAcceptRequest,
    InviteCreatedResponse,
    InviteCreateRequest,
    InviteListRead,
    InviteValidateResponse,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    ResetPasswordRequest,
    UserListRead,
)
from app.domains.auth.service import AuthService, AuthServiceError, LoginRateLimiter

router = APIRouter(prefix="/auth", tags=["auth"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


def _raise_auth_error(exc: AuthServiceError) -> None:
    headers = {"WWW-Authenticate": "Session"} if exc.status_code == 401 else None
    if exc.status_code == 429:
        headers = {"Retry-After": str(max(1, settings.auth_login_rate_window_seconds))}
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "message": exc.message, "retryable": False},
        headers=headers,
    ) from exc


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=raw_token,
        max_age=max(1, settings.auth_session_max_days) * 24 * 60 * 60,
        httponly=True,
        secure=settings.auth_cookie_secure or settings.app_env != "development",
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=settings.auth_csrf_cookie_name,
        value=secrets.token_urlsafe(32),
        max_age=max(1, settings.auth_session_max_days) * 24 * 60 * 60,
        httponly=False,
        secure=settings.auth_cookie_secure or settings.app_env != "development",
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.auth_cookie_name, path="/")
    response.delete_cookie(key=settings.auth_csrf_cookie_name, path="/")


def _auth_user(db: Session, user_id: str) -> AuthUserRead:
    service = AuthService(db)
    user = service.get_user(user_id)
    if user is None:
        # The development-only X-User-ID/Bearer compatibility path has no row.
        return AuthUserRead(
            id=user_id,
            display_name="本地开发用户",
            status="active",
            roles=["user"],
        )
    return AuthUserRead.model_validate(service.user_read(user))


def require_platform_admin(
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> str:
    if not AuthService(db).is_platform_admin(user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "admin_required",
                "message": "只有平台管理员可以执行此操作",
                "retryable": False,
            },
        )
    return user_id


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    rate_key = f"{request.client.host if request.client else 'unknown'}:{payload.email.strip().casefold()[:320]}"
    if not LoginRateLimiter.allow(rate_key):
        _raise_auth_error(
            AuthServiceError(
                "login_rate_limited",
                "登录尝试过于频繁，请稍后再试",
                status_code=429,
            )
        )
    try:
        service = AuthService(db)
        user = service.authenticate(payload.email, payload.password)
        raw_token, _ = service.create_session(
            user.id,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
        service.audit(user.id, "login_succeeded")
    except AuthServiceError as exc:
        _raise_auth_error(exc)
    LoginRateLimiter.clear(rate_key)
    _set_session_cookie(response, raw_token)
    return LoginResponse(user=AuthUserRead.model_validate(service.user_read(user)))


@router.post("/logout", response_model=MessageResponse)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> MessageResponse:
    raw_token = request.cookies.get(settings.auth_cookie_name)
    service = AuthService(db)
    service.revoke_session(raw_token, getattr(request.state, "session_id", None))
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        service.audit(user_id, "logout")
    _clear_session_cookie(response)
    return MessageResponse(message="已退出登录")


@router.post("/logout-all", response_model=MessageResponse)
def logout_all(
    response: Response,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    service = AuthService(db)
    service.revoke_all_sessions(user_id)
    service.audit(user_id, "logout_all")
    _clear_session_cookie(response)
    return MessageResponse(message="已退出所有设备")


@router.get("/me", response_model=AuthUserRead)
def me(user_id: str = Depends(get_current_user), db: Session = Depends(get_db)) -> AuthUserRead:
    return _auth_user(db, user_id)


@router.get("/invites/validate", response_model=InviteValidateResponse)
def validate_invite(token: str, db: Session = Depends(get_db)) -> InviteValidateResponse:
    try:
        invite = AuthService(db).validate_invite(token)
    except AuthServiceError as exc:
        return InviteValidateResponse(valid=False, message=exc.message)
    return InviteValidateResponse(
        valid=True,
        email=invite.email,
        expires_at=invite.expires_at,
    )


@router.post("/invites/accept", response_model=LoginResponse)
def accept_invite(
    payload: InviteAcceptRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    try:
        user, raw_token = AuthService(db).accept_invite(
            raw_token=payload.token,
            password=payload.password,
            display_name=payload.display_name,
        )
    except AuthServiceError as exc:
        _raise_auth_error(exc)
    _set_session_cookie(response, raw_token)
    return LoginResponse(user=AuthUserRead.model_validate(AuthService(db).user_read(user)))


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(
    payload: ForgotPasswordRequest, db: Session = Depends(get_db)
) -> ForgotPasswordResponse:
    try:
        token = AuthService(db).create_password_reset(payload.email)
    except AuthServiceError as exc:
        _raise_auth_error(exc)
    # Production sends this token through the configured email provider in the
    # deployment layer. Local development exposes it so the flow is testable.
    return ForgotPasswordResponse(
        message="如果该邮箱存在，我们会发送密码重置链接",
        debug_token=token if settings.is_dev else None,
    )


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(
    payload: ResetPasswordRequest, db: Session = Depends(get_db)
) -> MessageResponse:
    try:
        AuthService(db).reset_password(payload.token, payload.password)
    except AuthServiceError as exc:
        _raise_auth_error(exc)
    return MessageResponse(message="密码已重置，请重新登录")


@router.post("/change-password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MessageResponse:
    try:
        AuthService(db).change_password(user_id, payload.current_password, payload.new_password)
    except AuthServiceError as exc:
        _raise_auth_error(exc)
    return MessageResponse(message="密码已更新，请使用新密码重新登录")


@admin_router.post("/invites", response_model=InviteCreatedResponse, status_code=201)
def create_invite(
    payload: InviteCreateRequest,
    admin_id: str = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> InviteCreatedResponse:
    try:
        invite, raw_token = AuthService(db).create_invite(
            invited_by_user_id=admin_id,
            email=payload.email,
        )
    except AuthServiceError as exc:
        _raise_auth_error(exc)
    return InviteCreatedResponse(
        id=invite.id,
        email=invite.email,
        expires_at=invite.expires_at,
        token=raw_token,
    )


@admin_router.get("/invites", response_model=list[InviteListRead])
def list_invites(
    admin_id: str = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> list[InviteListRead]:
    del admin_id
    invites = db.scalars(select(UserInvite).order_by(UserInvite.created_at.desc()).limit(100)).all()
    return [InviteListRead.model_validate(invite) for invite in invites]


@admin_router.post("/invites/{invite_id}/revoke", response_model=MessageResponse)
def revoke_invite(
    invite_id: str,
    admin_id: str = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> MessageResponse:
    try:
        AuthService(db).revoke_invite(invite_id, admin_id)
    except AuthServiceError as exc:
        _raise_auth_error(exc)
    return MessageResponse(message="邀请已撤销")


@admin_router.get("/users", response_model=list[UserListRead])
def list_users(
    admin_id: str = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> list[UserListRead]:
    service = AuthService(db)
    users = db.scalars(select(User).order_by(User.created_at.desc()).limit(200)).all()
    del admin_id
    return [
        UserListRead(
            id=user.id,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            account_type=user.account_type,
            roles=service.roles(user.id),
            last_login_at=user.last_login_at,
        )
        for user in users
    ]


@admin_router.post("/users/{user_id}/disable", response_model=MessageResponse)
def disable_user(
    user_id: str,
    admin_id: str = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> MessageResponse:
    try:
        AuthService(db).set_user_status(user_id, "disabled", admin_id)
    except AuthServiceError as exc:
        _raise_auth_error(exc)
    return MessageResponse(message="账号已禁用，现有登录状态已撤销")


@admin_router.post("/users/{user_id}/enable", response_model=MessageResponse)
def enable_user(
    user_id: str,
    admin_id: str = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> MessageResponse:
    try:
        AuthService(db).set_user_status(user_id, "active", admin_id)
    except AuthServiceError as exc:
        _raise_auth_error(exc)
    return MessageResponse(message="账号已启用")


@admin_router.get("/audit", response_model=list[AuditEventRead])
def list_audit_events(
    admin_id: str = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> list[AuditEventRead]:
    from app.domains.auth.models import AuthAuditEvent

    del admin_id
    events = db.scalars(
        select(AuthAuditEvent).order_by(AuthAuditEvent.created_at.desc()).limit(200)
    ).all()
    return [AuditEventRead.model_validate(event) for event in events]
