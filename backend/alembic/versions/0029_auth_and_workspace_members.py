"""Add invitation authentication and explicit workspace membership.

Revision ID: 0029_auth_and_workspace_members
Revises: 0028_search_acl
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import UUIDString


revision: str = "0029_auth_and_workspace_members"
down_revision: str | None = "0028_search_acl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_OWNER_ID = "00000000-0000-0000-0000-000000000001"
LEGACY_OWNER_EMAIL = "legacy-owner@system.gapmind"


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_workspaces_is_demo", "workspaces", ["is_demo"])
    op.alter_column("workspaces", "is_demo", server_default=None)

    op.create_table(
        "users",
        sa.Column("id", UUIDString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=True),
        sa.Column("account_type", sa.String(length=32), nullable=False, server_default="human"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="invited"),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email_normalized", "users", ["email_normalized"], unique=True)
    op.create_index("ix_users_status", "users", ["status"])

    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "role"),
    )
    op.create_index("ix_user_roles_role", "user_roles", ["role"])

    op.create_table(
        "user_invites",
        sa.Column("id", UUIDString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_normalized", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("invited_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
        sa.Column("workspace_role", sa.String(length=32), nullable=False, server_default="viewer"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_invites_email_normalized", "user_invites", ["email_normalized"])
    op.create_index("ix_user_invites_token_hash", "user_invites", ["token_hash"], unique=True)
    op.create_index("ix_user_invites_expires_at", "user_invites", ["expires_at"])
    op.create_index("ix_user_invites_workspace_id", "user_invites", ["workspace_id"])

    op.create_table(
        "user_sessions",
        sa.Column("id", UUIDString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_hash", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True)
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", UUIDString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True)
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])

    op.create_table(
        "auth_audit_events",
        sa.Column("id", UUIDString(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_audit_events_user_id", "auth_audit_events", ["user_id"])
    op.create_index("ix_auth_audit_events_event_type", "auth_audit_events", ["event_type"])

    op.create_table(
        "workspace_members",
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="viewer"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "user_id"),
    )
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])
    op.create_index("ix_workspace_members_workspace_id", "workspace_members", ["workspace_id"])

    # Preserve existing content without making an administrator its owner.
    op.execute(
        sa.text(
            """
            INSERT INTO users
                (id, email, email_normalized, display_name, account_type, status)
            VALUES
                (:id, :email, :email_normalized, :display_name, 'system', 'active')
            """
        ).bindparams(
            id=LEGACY_OWNER_ID,
            email=LEGACY_OWNER_EMAIL,
            email_normalized=LEGACY_OWNER_EMAIL,
            display_name="历史内容归属",
        )
    )
    for table in (
        "workspaces",
        "chat_conversations",
        "paper_search_histories",
        "paper_search_favorites",
    ):
        op.execute(
            sa.text(f"UPDATE {table} SET owner_id = :legacy_id WHERE owner_id = 'user'").bindparams(
                legacy_id=LEGACY_OWNER_ID
            )
        )
    op.execute(
        sa.text("UPDATE workspaces SET is_demo = true WHERE owner_id = :legacy_id").bindparams(
            legacy_id=LEGACY_OWNER_ID
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO workspace_members (workspace_id, user_id, role)
            SELECT id, :legacy_id, 'owner'
            FROM workspaces
            WHERE is_deleted = false
              AND NOT EXISTS (
                SELECT 1 FROM workspace_members wm
                WHERE wm.workspace_id = workspaces.id AND wm.user_id = :legacy_id
              )
            """
        ).bindparams(legacy_id=LEGACY_OWNER_ID)
    )


def downgrade() -> None:
    # Restore the legacy owner sentinel before removing the compatibility
    # identity. This is only a schema rollback; no content rows are deleted.
    for table in (
        "workspaces",
        "chat_conversations",
        "paper_search_histories",
        "paper_search_favorites",
    ):
        op.execute(
            sa.text(f"UPDATE {table} SET owner_id = 'user' WHERE owner_id = :legacy_id").bindparams(
                legacy_id=LEGACY_OWNER_ID
            )
        )

    op.drop_index("ix_workspace_members_workspace_id", table_name="workspace_members")
    op.drop_index("ix_workspace_members_user_id", table_name="workspace_members")
    op.drop_table("workspace_members")

    op.drop_index("ix_auth_audit_events_event_type", table_name="auth_audit_events")
    op.drop_index("ix_auth_audit_events_user_id", table_name="auth_audit_events")
    op.drop_table("auth_audit_events")

    op.drop_index("ix_password_reset_tokens_expires_at", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_index("ix_user_sessions_token_hash", table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index("ix_user_invites_workspace_id", table_name="user_invites")
    op.drop_index("ix_user_invites_expires_at", table_name="user_invites")
    op.drop_index("ix_user_invites_token_hash", table_name="user_invites")
    op.drop_index("ix_user_invites_email_normalized", table_name="user_invites")
    op.drop_table("user_invites")

    op.drop_index("ix_user_roles_role", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_index("ix_users_email_normalized", table_name="users")
    op.drop_table("users")
    op.drop_index("ix_workspaces_is_demo", table_name="workspaces")
    op.drop_column("workspaces", "is_demo")
