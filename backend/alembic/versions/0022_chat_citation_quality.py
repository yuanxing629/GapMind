"""持久化有界 Chat 引用质量审计。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0022_chat_citation_quality"
down_revision: str | None = "0021_gap_remote_fallback"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column(
            "citation_quality",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("chat_messages", "citation_quality", server_default=None)


def downgrade() -> None:
    op.drop_column("chat_messages", "citation_quality")
