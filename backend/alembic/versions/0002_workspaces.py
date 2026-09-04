"""创建 workspaces 表。

Revision ID：0002_workspaces
Revises：0001_initial
创建日期：2026-07-18

Phase 1a：第一张领域表。Workspace 是核心范围对象；未来每张领域表
（artifacts、papers、knowledge_items、tasks、timeline_events）都将携带指回此表的 workspace_id FK。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from app.db.base import UUIDString

# revision 标识，供 Alembic 使用。
revision: str = "0002_workspaces"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("keywords", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("goals", sa.Text(), nullable=True),
        sa.Column("constraints", sa.Text(), nullable=True),
        sa.Column("active_questions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_workspaces_is_archived", "workspaces", ["is_archived"])
    op.create_index("ix_workspaces_is_deleted", "workspaces", ["is_deleted"])


def downgrade() -> None:
    op.drop_index("ix_workspaces_is_deleted", table_name="workspaces")
    op.drop_index("ix_workspaces_is_archived", table_name="workspaces")
    op.drop_table("workspaces")
