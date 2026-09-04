"""在论文记录上持久化 PDF 解析质量反馈。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0026_paper_parse_quality"
down_revision: str | None = "0025_workspace_owner_acl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("papers", sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("papers", sa.Column("parsed_text_chars", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("papers", sa.Column("quality_flags", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("papers", sa.Column("parse_error", sa.Text(), nullable=True))
    op.alter_column("papers", "page_count", server_default=None)
    op.alter_column("papers", "parsed_text_chars", server_default=None)
    op.alter_column("papers", "quality_flags", server_default=None)


def downgrade() -> None:
    op.drop_column("papers", "parse_error")
    op.drop_column("papers", "quality_flags")
    op.drop_column("papers", "parsed_text_chars")
    op.drop_column("papers", "page_count")
