"""邀请认证、session 和个人 workspace 测试。"""

from __future__ import annotations

from datetime import UTC, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domains.auth.models import User, UserRole, UserSession
from app.domains.auth.service import AuthService, AuthServiceError, PASSWORD_HASHER
from app.domains.workspace.models import Workspace
from app.domains.workspace.schemas import WorkspaceCreate
from app.domains.workspace.service import WorkspaceNotFoundError, WorkspaceService


def _admin(db_session) -> User:
    user = User(
        id=str(uuid4()),
        email="admin@example.com",
        email_normalized="admin@example.com",
        display_name="Admin",
        account_type="human",
        status="active",
        password_hash=PASSWORD_HASHER.hash("admin-pass"),
    )
    db_session.add(user)
    db_session.add(UserRole(user_id=user.id, role="platform_admin"))
    db_session.commit()
    return user


def test_invitation_creates_single_use_session_and_can_logout(client: TestClient, db_session) -> None:
    admin = _admin(db_session)
    create = client.post(
        "/api/v1/admin/invites",
        headers={"X-User-ID": admin.id},
        json={"email": "researcher@example.com"},
    )
    assert create.status_code == 201, create.text
    token = create.json()["token"]
    assert token

    valid = client.get("/api/v1/auth/invites/validate", params={"token": token})
    assert valid.status_code == 200
    assert valid.json()["valid"] is True

    accepted = client.post(
        "/api/v1/auth/invites/accept",
        json={"token": token, "password": "a very long passphrase", "display_name": "Researcher"},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["user"]["email"] == "researcher@example.com"
    assert "a very long passphrase" not in accepted.text
    assert db_session.scalar(select(User).where(User.email_normalized == "researcher@example.com")) is not None
    stored_session = db_session.scalar(select(UserSession))
    assert stored_session is not None
    assert stored_session.token_hash not in accepted.text

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["display_name"] == "Researcher"

    reused = client.post(
        "/api/v1/auth/invites/accept",
        json={"token": token, "password": "another passphrase"},
    )
    assert reused.status_code == 400
    assert reused.json()["detail"]["error"] == "invite_invalid"

    missing_csrf = client.post("/api/v1/auth/logout")
    assert missing_csrf.status_code == 403
    from app.core.config import settings

    csrf = client.cookies.get(settings.auth_csrf_cookie_name)
    logged_out = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf or ""})
    assert logged_out.status_code == 200
    assert stored_session.revoked_at is not None


def test_login_with_password_sets_cookie_and_wrong_password_is_rejected(client: TestClient, db_session) -> None:
    user = User(
        id=str(uuid4()),
        email="login@example.com",
        email_normalized="login@example.com",
        status="active",
        password_hash=PASSWORD_HASHER.hash("correct"),
    )
    db_session.add(user)
    db_session.add(UserRole(user_id=user.id, role="user"))
    db_session.commit()

    wrong = client.post("/api/v1/auth/login", json={"email": user.email, "password": "wrong"})
    assert wrong.status_code == 401
    correct = client.post("/api/v1/auth/login", json={"email": user.email, "password": "correct"})
    assert correct.status_code == 200
    assert "gm_session" in correct.cookies


def test_workspace_owner_can_read_but_other_user_cannot(db_session) -> None:
    service = WorkspaceService(db_session)
    workspace = service.create(WorkspaceCreate(name="Personal study"), owner_id="owner-id")

    assert service.get(workspace.id, actor_id="owner-id").id == workspace.id
    assert service.list(owner_id="owner-id")[1] == 1
    assert service.list(owner_id="member-id")[1] == 0
    try:
        service.get(workspace.id, actor_id="outsider-id")
    except WorkspaceNotFoundError:
        pass
    else:
        raise AssertionError("a non-owner must not read a workspace")


def test_non_owner_cannot_use_workspace_paper_route(
    client: TestClient, db_session
) -> None:
    workspace = Workspace(
        id=str(uuid4()),
        name="Owner-only corpus",
        owner_id="workspace-owner",
        is_archived=False,
        is_deleted=False,
    )
    db_session.add(workspace)
    db_session.commit()

    response = client.post(
        f"/api/v1/workspaces/{workspace.id}/papers",
        headers={"X-User-ID": "different-user"},
        json={"title": "Should be blocked", "authors": []},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "workspace_not_found"


def test_invited_user_without_workspace_gets_no_workspace_access(
    client: TestClient, db_session
) -> None:
    admin = _admin(db_session)
    db_session.add_all(
        [
            Workspace(
                id=str(uuid4()),
                name=f"Legacy corpus {index}",
                owner_id="legacy-owner",
                is_demo=True,
                is_archived=False,
                is_deleted=False,
            )
            for index in range(2)
        ]
    )
    db_session.commit()
    invite = client.post(
        "/api/v1/admin/invites",
        headers={"X-User-ID": admin.id},
        json={"email": "demo-user@example.com"},
    ).json()
    accepted = client.post(
        "/api/v1/auth/invites/accept",
        json={"token": invite["token"], "password": "demo-pass"},
    )
    assert accepted.status_code == 200

    visible = client.get("/api/v1/workspaces")
    assert visible.status_code == 200, visible.text
    assert visible.json()["total"] == 0

def test_session_expiry_and_password_reset_are_one_time(db_session) -> None:
    user = User(
        id=str(uuid4()),
        email="reset@example.com",
        email_normalized="reset@example.com",
        status="active",
        password_hash=PASSWORD_HASHER.hash("old-pass"),
    )
    db_session.add(user)
    db_session.commit()
    service = AuthService(db_session)
    raw_session, session = service.create_session(user.id)
    session.last_seen_at = session.last_seen_at.replace(tzinfo=UTC) - timedelta(hours=24)
    session.expires_at = session.expires_at.replace(tzinfo=UTC) - timedelta(minutes=1)
    db_session.commit()
    assert service.resolve_session(raw_session) is None

    reset_token = service.create_password_reset(user.email)
    assert reset_token
    service.reset_password(reset_token, "new-pass")
    with pytest.raises(AuthServiceError) as error:
        service.reset_password(reset_token, "third-pass")
    assert error.value.code == "reset_token_invalid"


def test_admin_can_list_and_disable_user(db_session) -> None:
    admin = _admin(db_session)
    user = User(
        id=str(uuid4()),
        email="disable@example.com",
        email_normalized="disable@example.com",
        status="active",
        password_hash=PASSWORD_HASHER.hash("pass"),
    )
    db_session.add(user)
    db_session.commit()
    service = AuthService(db_session)
    raw_session, _ = service.create_session(user.id)
    service.set_user_status(user.id, "disabled", admin.id)
    assert service.resolve_session(raw_session) is None
    assert db_session.get(User, user.id).status == "disabled"


def test_admin_audit_endpoint_serializes_audit_events(client: TestClient, db_session) -> None:
    admin = _admin(db_session)
    AuthService(db_session).audit(admin.id, "login_succeeded")

    response = client.get("/api/v1/admin/audit", headers={"X-User-ID": admin.id})

    assert response.status_code == 200, response.text
    assert response.json()[0]["event_type"] == "login_succeeded"


def test_admin_invite_endpoint_serializes_invites(client: TestClient, db_session) -> None:
    admin = _admin(db_session)
    AuthService(db_session).create_invite(
        invited_by_user_id=admin.id,
        email="listed@example.com",
    )

    response = client.get("/api/v1/admin/invites", headers={"X-User-ID": admin.id})

    assert response.status_code == 200, response.text
    assert response.json()[0]["email"] == "listed@example.com"
