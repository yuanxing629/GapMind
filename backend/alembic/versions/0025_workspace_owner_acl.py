"""为工作区增加所有者身份，用于交付时隔离。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0025_workspace_owner_acl"
down_revision: str | None = "0024_chat_generation_observability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workspaces",
        sa.Column("owner_id", sa.String(length=128), nullable=False, server_default="user"),
    )
    op.create_index("ix_workspaces_owner_id", "workspaces", ["owner_id"], unique=False)
    op.alter_column("workspaces", "owner_id", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_workspaces_owner_id", table_name="workspaces")
    op.drop_column("workspaces", "owner_id")
