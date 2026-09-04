"""向 papers 表增加 parsed_markdown_artifact_id。

Revision ID：0005_paper_markdown
Revises：0004_paper_parse_state
创建日期：2026-07-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0005_paper_markdown"
down_revision: Union[str, None] = "0004_paper_parse_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column(
            "parsed_markdown_artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_papers_parsed_markdown_artifact_id",
        "papers",
        ["parsed_markdown_artifact_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_papers_parsed_markdown_artifact_id", table_name="papers")
    op.drop_column("papers", "parsed_markdown_artifact_id")
