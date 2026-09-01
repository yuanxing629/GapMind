"""Authentication services with server-side sessions and one-time tokens."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from redis import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.auth.models import (
    AuthAuditEvent,
    PasswordResetToken,
    User,
    UserInvite,
    UserRole,
    UserSession,
)

PASSWORD_HASHER = PasswordHasher()
PLATFORM_ADMIN_ROLE = "platform_admin"
USER_ROLE = "user"
WORKSPACE_ROLES = {"viewer", "editor", "owner"}


class AuthServiceError(Exception):
    """An expected authentication failure suitable for an HTTP response."""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LoginRateLimiter:
    """Small process-local fallback limiter for login attempts.

    Production deployments should point all API workers at the same Redis
    rate-limit layer. The local fallback still protects a single worker and
    keeps tests and offline development independent of Redis availability.
    """

    _lock = threading.Lock()
    _attempts: dict[str, list[float]] = {}

    @classmethod
    def _redis_key(cls, key: str) -> str:
        return "gapmind:auth:login:" + hashlib.sha256(key.encode("utf-8")).hexdigest()

    @classmethod
    def allow(cls, key: str) -> bool:
        now = time.monotonic()
        window = max(1, settings.auth_login_rate_window_seconds)
        try:
            client = Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
            )
            redis_key = cls._redis_key(key)
            current = int(client.get(redis_key) or 0)
            if current >= max(1, settings.auth_login_rate_limit):
                client.close()
                return False
            pipe = client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, window)
            pipe.execute()
            client.close()
            return True
        except Exception:
            # Redis is an operational dependency, but a local process can
            # still fail closed enough for development if Redis is restarting.
            pass
        with cls._lock:
            values = [
                timestamp
                for timestamp in cls._attempts.get(key, [])
                if timestamp > now - window
            ]
            if len(values) >= max(1, settings.auth_login_rate_limit):
                cls._attempts[key] = values
                return False
            values.append(now)
            cls._attempts[key] = values
            return True

    @classmethod
    def clear(cls, key: str) -> None:
        try:
            client = Redis.from_url(
                settings.redis_url,
                socket_connect_timeout=0.2,
                socket_timeout=0.2,
            )
            client.delete(cls._redis_key(key))
            client.close()
        except Exception:
            pass
        with cls._lock:
            cls._attempts.pop(key, None)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def normalize_email(email: str) -> str:
    normalized = email.strip().casefold()
    if not normalized or "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
        raise AuthServiceError("invalid_email", "请输入有效的邮箱地址")
    return normalized


def validate_password(password: str) -> None:
    if not password:
        raise AuthServiceError("password_required", "密码不能为空")
    if (
        settings.auth_max_password_bytes > 0
        and len(password.encode("utf-8")) > settings.auth_max_password_bytes
    ):
        raise AuthServiceError(
            "password_too_long",
            f"密码不能超过 {settings.auth_max_password_bytes} 字节",
        )


def _password_hash(password: str) -> str:
    validate_password(password)
    return PASSWORD_HASHER.hash(password)


def _token_digest(raw_token: str) -> str:
    secret = settings.auth_session_secret.encode("utf-8")
    return hmac.new(secret, raw_token.encode("utf-8"), hashlib.sha256).hexdigest()


def _ip_digest(ip_address: str | None) -> str | None:
    if not ip_address:
        return None
    return _token_digest(f"ip:{ip_address}")


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # --------------------------------------------------------------- identities
    def get_user(self, user_id: str) -> User | None:
        return self.db.get(User, user_id)

    def get_user_by_email(self, email: str) -> User | None:
        normalized = normalize_email(email)
        return self.db.scalar(select(User).where(User.email_normalized == normalized))

    def roles(self, user_id: str) -> list[str]:
        return list(
            self.db.scalars(
                select(UserRole.role).where(UserRole.user_id == user_id).order_by(UserRole.role)
            ).all()
        )

    def is_platform_admin(self, user_id: str) -> bool:
        return self.db.scalar(
            select(UserRole.user_id).where(
                UserRole.user_id == user_id,
                UserRole.role == PLATFORM_ADMIN_ROLE,
            )
        ) is not None

    def user_read(self, user: User) -> dict:
        roles = self.roles(user.id)
        return {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "status": user.status,
            "roles": roles,
            "is_platform_admin": PLATFORM_ADMIN_ROLE in roles,
        }

    def authenticate(self, email: str, password: str) -> User:
        user = self.get_user_by_email(email)
        if user is None or user.status != "active" or not user.password_hash:
            raise AuthServiceError(
                "invalid_credentials", "邮箱或密码不正确", status_code=401
            )
        try:
            PASSWORD_HASHER.verify(user.password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            raise AuthServiceError(
                "invalid_credentials", "邮箱或密码不正确", status_code=401
            ) from None
        user.last_login_at = utcnow()
        self.db.flush()
        return user

    # ---------------------------------------------------------------- sessions
    def create_session(
        self,
        user_id: str,
        *,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[str, UserSession]:
        now = utcnow()
        raw_token = secrets.token_urlsafe(48)
        session = UserSession(
            id=str(uuid4()),
            user_id=user_id,
            token_hash=_token_digest(raw_token),
            expires_at=now + timedelta(days=max(1, settings.auth_session_max_days)),
            last_seen_at=now,
            user_agent=(user_agent or "")[:512] or None,
            ip_hash=_ip_digest(ip_address),
        )
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return raw_token, session

    def resolve_session(self, raw_token: str) -> tuple[str, str] | None:
        if not raw_token:
            return None
        session = self.db.scalar(
            select(UserSession).where(UserSession.token_hash == _token_digest(raw_token))
        )
        now = utcnow()
        if (
            session is None
            or session.revoked_at is not None
            or _aware(session.expires_at) <= now
            or _aware(session.last_seen_at) + timedelta(hours=max(1, settings.auth_session_idle_hours)) <= now
        ):
            return None
        user = self.db.get(User, session.user_id)
        if user is None or user.status != "active":
            return None
        # Avoid a write on every request while still extending activity state.
        if _aware(session.last_seen_at) + timedelta(minutes=1) <= now:
            session.last_seen_at = now
            self.db.commit()
        return user.id, session.id

    def revoke_session(self, raw_token: str | None = None, session_id: str | None = None) -> None:
        if not raw_token and not session_id:
            return
        session = None
        if session_id:
            session = self.db.get(UserSession, session_id)
        elif raw_token:
            session = self.db.scalar(
                select(UserSession).where(UserSession.token_hash == _token_digest(raw_token))
            )
        if session is not None and session.revoked_at is None:
            session.revoked_at = utcnow()
            self.db.commit()

    def revoke_all_sessions(self, user_id: str) -> None:
        self.db.execute(
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=utcnow())
        )
        self.db.commit()

    def set_user_status(self, user_id: str, status: str, admin_id: str) -> None:
        if status not in {"active", "disabled"}:
            raise AuthServiceError("invalid_user_status", "用户状态不受支持")
        if user_id == admin_id and status == "disabled":
            raise AuthServiceError("cannot_disable_self", "不能禁用当前管理员账号", status_code=409)
        user = self.db.get(User, user_id)
        if user is None:
            raise AuthServiceError("user_not_found", "账号不存在", status_code=404)
        if status == "disabled" and self.is_platform_admin(user_id):
            admin_count = self.db.scalar(
                select(UserRole.user_id)
                .join(User, User.id == UserRole.user_id)
                .where(UserRole.role == PLATFORM_ADMIN_ROLE, User.status == "active")
                .limit(2)
            )
            if admin_count is None:
                raise AuthServiceError("admin_not_found", "管理员账号不存在", status_code=409)
            active_admins = len(
                self.db.scalars(
                    select(UserRole.user_id)
                    .join(User, User.id == UserRole.user_id)
                    .where(UserRole.role == PLATFORM_ADMIN_ROLE, User.status == "active")
                ).all()
            )
            if active_admins <= 1:
                raise AuthServiceError("last_admin", "不能禁用最后一个平台管理员", status_code=409)
        user.status = status
        self.db.commit()
        if status == "disabled":
            self.revoke_all_sessions(user_id)
        self.audit(admin_id, f"user_{status}", user_id)

    # ---------------------------------------------------------------- invites
    def create_invite(
        self,
        *,
        invited_by_user_id: str,
        email: str,
    ) -> tuple[UserInvite, str]:
        normalized = normalize_email(email)
        existing = self.db.scalar(select(User).where(User.email_normalized == normalized))
        if existing is not None and existing.status == "active":
            raise AuthServiceError("user_already_active", "该邮箱已经存在可用账号", status_code=409)

        self.db.execute(
            update(UserInvite)
            .where(
                UserInvite.email_normalized == normalized,
                UserInvite.accepted_at.is_(None),
                UserInvite.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
        raw_token = secrets.token_urlsafe(48)
        invite = UserInvite(
            id=str(uuid4()),
            email=email.strip(),
            email_normalized=normalized,
            token_hash=_token_digest(raw_token),
            invited_by_user_id=invited_by_user_id,
            expires_at=utcnow() + timedelta(hours=max(1, settings.auth_invite_ttl_hours)),
        )
        self.db.add(invite)
        self.db.commit()
        self.db.refresh(invite)
        self.audit(invited_by_user_id, "invite_created", invite.id)
        return invite, raw_token

    def validate_invite(self, raw_token: str) -> UserInvite:
        invite = self.db.scalar(
            select(UserInvite).where(UserInvite.token_hash == _token_digest(raw_token))
        )
        if (
            invite is None
            or invite.accepted_at is not None
            or invite.revoked_at is not None
            or _aware(invite.expires_at) <= utcnow()
        ):
            raise AuthServiceError("invite_invalid", "邀请链接无效或已过期", status_code=400)
        return invite

    def accept_invite(
        self,
        *,
        raw_token: str,
        password: str,
        display_name: str | None = None,
    ) -> tuple[User, str]:
        invite = self.db.scalar(
            select(UserInvite)
            .where(UserInvite.token_hash == _token_digest(raw_token))
            .with_for_update()
        )
        if (
            invite is None
            or invite.accepted_at is not None
            or invite.revoked_at is not None
            or _aware(invite.expires_at) <= utcnow()
        ):
            raise AuthServiceError("invite_invalid", "邀请链接无效或已过期", status_code=400)
        validate_password(password)
        user = self.db.scalar(select(User).where(User.email_normalized == invite.email_normalized))
        if user is not None and user.status == "active" and user.password_hash:
            raise AuthServiceError("user_already_active", "该邀请对应的账号已经激活", status_code=409)
        if user is None:
            user = User(
                id=str(uuid4()),
                email=invite.email,
                email_normalized=invite.email_normalized,
                display_name=(display_name or "").strip()[:128] or None,
                account_type="human",
                status="active",
                password_hash=_password_hash(password),
                password_changed_at=utcnow(),
            )
            self.db.add(user)
            self.db.flush()
        else:
            user.email = invite.email
            user.display_name = (display_name or user.display_name or "").strip()[:128] or None
            user.status = "active"
            user.password_hash = _password_hash(password)
            user.password_changed_at = utcnow()

        if not self.db.scalar(
            select(UserRole.user_id).where(UserRole.user_id == user.id, UserRole.role == USER_ROLE)
        ):
            self.db.add(UserRole(user_id=user.id, role=USER_ROLE))
        # Invitations create an account only. Workspaces are personal and can
        # be accessed only after this user creates them.
        invite.accepted_at = utcnow()
        self.db.commit()
        self.db.refresh(user)
        raw_session, _ = self.create_session(user.id)
        self.audit(user.id, "invite_accepted", invite.id)
        return user, raw_session

    def revoke_invite(self, invite_id: str, admin_id: str) -> None:
        invite = self.db.get(UserInvite, invite_id)
        if invite is None:
            raise AuthServiceError("invite_not_found", "邀请不存在", status_code=404)
        if invite.accepted_at is not None:
            raise AuthServiceError("invite_already_accepted", "已接受的邀请不能撤销", status_code=409)
        if invite.revoked_at is None:
            invite.revoked_at = utcnow()
            self.db.commit()
            self.audit(admin_id, "invite_revoked", invite.id)

    # ------------------------------------------------------------ password flow
    def create_password_reset(self, email: str) -> str | None:
        normalized = normalize_email(email)
        user = self.db.scalar(
            select(User).where(User.email_normalized == normalized, User.status == "active")
        )
        if user is None:
            return None
        self.db.execute(
            delete(PasswordResetToken).where(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
            )
        )
        raw_token = secrets.token_urlsafe(48)
        self.db.add(
            PasswordResetToken(
                id=str(uuid4()),
                user_id=user.id,
                token_hash=_token_digest(raw_token),
                expires_at=utcnow() + timedelta(minutes=max(5, settings.auth_password_reset_ttl_minutes)),
            )
        )
        self.db.commit()
        return raw_token

    def reset_password(self, raw_token: str, password: str) -> User:
        validate_password(password)
        reset = self.db.scalar(
            select(PasswordResetToken).where(
                PasswordResetToken.token_hash == _token_digest(raw_token)
            )
        )
        if reset is None or reset.used_at is not None or _aware(reset.expires_at) <= utcnow():
            raise AuthServiceError("reset_token_invalid", "重置链接无效或已过期", status_code=400)
        user = self.db.get(User, reset.user_id)
        if user is None or user.status != "active":
            raise AuthServiceError("reset_token_invalid", "重置链接无效或已过期", status_code=400)
        user.password_hash = _password_hash(password)
        user.password_changed_at = utcnow()
        reset.used_at = utcnow()
        self.db.commit()
        self.revoke_all_sessions(user.id)
        self.audit(user.id, "password_reset", reset.id)
        return user

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        user = self.db.get(User, user_id)
        if user is None or not user.password_hash:
            raise AuthServiceError("user_not_found", "账号不存在", status_code=404)
        try:
            PASSWORD_HASHER.verify(user.password_hash, current_password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            raise AuthServiceError("invalid_current_password", "当前密码不正确", status_code=400) from None
        user.password_hash = _password_hash(new_password)
        user.password_changed_at = utcnow()
        self.db.commit()
        self.revoke_all_sessions(user_id)
        self.audit(user_id, "password_changed", user_id)

    # ------------------------------------------------------------------ audit
    def audit(self, user_id: str | None, event_type: str, target_id: str | None = None) -> None:
        self.db.add(
            AuthAuditEvent(
                id=str(uuid4()),
                user_id=user_id,
                event_type=event_type,
                target_id=target_id,
                metadata_json=json.dumps({}, ensure_ascii=False),
            )
        )
        self.db.commit()
