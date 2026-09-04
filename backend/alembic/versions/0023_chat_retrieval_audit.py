"""持久化非敏感的 Chat 检索可观测性数据。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0023_chat_retrieval_audit"
down_revision: str | None = "0022_chat_citation_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column(
            "retrieval_audit",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("chat_messages", "retrieval_audit", server_default=None)


def downgrade() -> None:
    op.drop_column("chat_messages", "retrieval_audit")
