"""增加外部论文搜索历史和收藏。

Revision ID：0008_search_history_favorites
Revises：0007_extraction_rejections
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from app.db.base import UUIDString

revision: str = "0008_search_history_favorites"
down_revision: Union[str, None] = "0007_extraction_rejections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_search_histories",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("query", sa.String(255), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("sort", sa.String(64), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_paper_search_histories_query", "paper_search_histories", ["query"])

    op.create_table(
        "paper_search_favorites",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("semantic_scholar_paper_id", sa.String(255), nullable=False, unique=True),
        sa.Column("paper", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_paper_search_favorites_semantic_scholar_paper_id",
        "paper_search_favorites",
        ["semantic_scholar_paper_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_paper_search_favorites_semantic_scholar_paper_id",
        table_name="paper_search_favorites",
    )
    op.drop_table("paper_search_favorites")
    op.drop_index("ix_paper_search_histories_query", table_name="paper_search_histories")
    op.drop_table("paper_search_histories")
