"""将外部论文搜索历史和收藏限定到所有者。

Revision ID：0028_search_acl
Revises：0027_chat_conversation_owner
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.base import UUIDString


revision: str = "0028_search_acl"
down_revision: str | None = "0027_chat_conversation_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "paper_search_histories",
        sa.Column("owner_id", sa.String(length=128), nullable=False, server_default="user"),
    )
    op.create_index(
        "ix_paper_search_histories_owner_id",
        "paper_search_histories",
        ["owner_id"],
    )
    op.alter_column("paper_search_histories", "owner_id", server_default=None)

    # 原收藏表使 semantic_scholar_paper_id 全局唯一。重建该表，使不同用户可以独立收藏同一论文，
    # 同时将所有现有行保留为旧用户记录。
    op.create_table(
        "paper_search_favorites_new",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("semantic_scholar_paper_id", sa.String(length=255), nullable=False),
        sa.Column("paper", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        sa.text(
            """
            INSERT INTO paper_search_favorites_new
                (id, owner_id, semantic_scholar_paper_id, paper, note, created_at, updated_at)
            SELECT id, 'user', semantic_scholar_paper_id, paper, note, created_at, updated_at
            FROM paper_search_favorites
            """
        )
    )
    op.drop_table("paper_search_favorites")
    op.rename_table("paper_search_favorites_new", "paper_search_favorites")
    op.create_index(
        "ix_paper_search_favorites_semantic_scholar_paper_id",
        "paper_search_favorites",
        ["semantic_scholar_paper_id"],
    )
    op.create_index(
        "ix_paper_search_favorites_owner_id",
        "paper_search_favorites",
        ["owner_id"],
    )
    op.create_index(
        "uq_paper_search_favorite_owner_paper",
        "paper_search_favorites",
        ["owner_id", "semantic_scholar_paper_id"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    duplicate = bind.execute(
        sa.text(
            """
            SELECT semantic_scholar_paper_id
            FROM paper_search_favorites
            GROUP BY semantic_scholar_paper_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade search ACL: multiple owners have the same favorite paper"
        )

    op.drop_index(
        "uq_paper_search_favorite_owner_paper",
        table_name="paper_search_favorites",
    )
    op.drop_index(
        "ix_paper_search_favorites_owner_id",
        table_name="paper_search_favorites",
    )
    op.drop_index(
        "ix_paper_search_favorites_semantic_scholar_paper_id",
        table_name="paper_search_favorites",
    )
    op.drop_column("paper_search_favorites", "owner_id")
    op.create_index(
        "ix_paper_search_favorites_semantic_scholar_paper_id",
        "paper_search_favorites",
        ["semantic_scholar_paper_id"],
        unique=True,
    )

    op.drop_index(
        "ix_paper_search_histories_owner_id",
        table_name="paper_search_histories",
    )
    op.drop_column("paper_search_histories", "owner_id")
