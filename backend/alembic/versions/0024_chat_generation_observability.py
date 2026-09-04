"""持久化非敏感的 Chat 生成耗时和大小指标。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0024_chat_generation_observability"
down_revision: str | None = "0023_chat_retrieval_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("prompt_chars", sa.Integer(), nullable=True))
    op.add_column("chat_messages", sa.Column("response_chars", sa.Integer(), nullable=True))
    op.add_column(
        "chat_messages",
        sa.Column("first_token_latency_ms", sa.Float(), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column("completion_latency_ms", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "completion_latency_ms")
    op.drop_column("chat_messages", "first_token_latency_ms")
    op.drop_column("chat_messages", "response_chars")
    op.drop_column("chat_messages", "prompt_chars")
