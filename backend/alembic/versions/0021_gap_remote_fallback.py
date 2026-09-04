"""记录 Gap 标注使用远程回退的原因。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0021_gap_remote_fallback"
down_revision: str | None = "0020_chat_retrieval_diagnostic"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "paper_gap_annotations",
        sa.Column("fallback_reason", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("paper_gap_annotations", "fallback_reason")
