"""Make workspaces personal and remove collaboration membership data."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0030_owner_only_workspaces"
down_revision: str | None = "0029_auth_and_workspace_members"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Workspace membership is no longer an authorization source. The table
    # contains only derived access metadata, not research content.
    op.drop_table("workspace_members")

    # Invitations now create accounts only. Existing invite history keeps its
    # email and lifecycle timestamps, while obsolete workspace targeting data
    # is removed from the schema.
    op.drop_index("ix_user_invites_workspace_id", table_name="user_invites")
    op.drop_column("user_invites", "workspace_role")
    op.drop_column("user_invites", "workspace_id")


def downgrade() -> None:
    # Downgrade restores the old shape for compatibility. Membership rows
    # cannot be reconstructed because owner-only mode intentionally removed
    # that derived collaboration metadata.
    op.add_column(
        "user_invites",
        sa.Column("workspace_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "user_invites",
        sa.Column(
            "workspace_role",
            sa.String(length=32),
            nullable=False,
            server_default="viewer",
        ),
    )
    op.create_index(
        "ix_user_invites_workspace_id", "user_invites", ["workspace_id"]
    )

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
