"""在 Chat 消息上持久化安全的工作区检索诊断信息。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0020_chat_retrieval_diagnostic"
down_revision: str | None = "0019_chat_message_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("retrieval_diagnostic_code", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "retrieval_diagnostic_code")
