"""将工作区设为个人空间并移除协作成员数据。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0030_owner_only_workspaces"
down_revision: str | None = "0029_auth_and_workspace_members"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
# 工作区成员关系不再作为授权来源。该表只包含派生访问元数据，不包含研究内容。
    op.drop_table("workspace_members")

# 邀请现在只创建账户。现有邀请历史保留邮箱和生命周期时间戳，
# 同时从 schema 中移除过时的工作区目标数据。
    op.drop_index("ix_user_invites_workspace_id", table_name="user_invites")
    op.drop_column("user_invites", "workspace_role")
    op.drop_column("user_invites", "workspace_id")


def downgrade() -> None:
# downgrade 为兼容性恢复旧结构。无法重建成员行，因为 owner-only 模式有意移除了
# 派生的协作元数据。
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
