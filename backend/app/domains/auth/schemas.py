"""邀请认证的 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AuthUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str | None = None
    display_name: str | None = None
    status: str
    roles: list[str] = Field(default_factory=list)
    is_platform_admin: bool = False


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    user: AuthUserRead


class InviteCreateRequest(BaseModel):
    email: str


class InviteCreatedResponse(BaseModel):
    id: str
    email: str
    expires_at: datetime
    token: str


class InviteListRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None


class UserListRead(BaseModel):
    id: str
    email: str
    display_name: str | None = None
    status: str
    account_type: str
    roles: list[str] = Field(default_factory=list)
    last_login_at: datetime | None = None


class AuditEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str | None = None
    event_type: str
    target_id: str | None = None
    created_at: datetime


class InviteValidateResponse(BaseModel):
    valid: bool
    email: str | None = None
    expires_at: datetime | None = None
    message: str | None = None


class InviteAcceptRequest(BaseModel):
    token: str
    password: str
    display_name: str | None = Field(default=None, max_length=128)


class MessageResponse(BaseModel):
    message: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ForgotPasswordResponse(BaseModel):
    message: str
    debug_token: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
