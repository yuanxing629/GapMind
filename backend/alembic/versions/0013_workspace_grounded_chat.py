"""为 AI Chat 增加工作区 grounding 和持久化引用。"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.base import UUIDString


revision: str = "0013_workspace_grounded_chat"
down_revision: Union[str, None] = "0012_discover_run_soft_delete"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_conversations",
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_chat_conversations_workspace_id",
        "chat_conversations",
        ["workspace_id"],
    )
    op.add_column(
        "chat_messages",
        sa.Column(
            "grounding_status",
            sa.String(length=32),
            nullable=False,
            server_default="not_requested",
        ),
    )
    op.create_table(
        "chat_message_evidence",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "message_id",
            UUIDString(),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_id",
            UUIDString(),
            sa.ForeignKey("papers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "artifact_id",
            UUIDString(),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chunk_id", sa.String(length=64), nullable=True),
        sa.Column("paper_title", sa.Text(), nullable=True),
        sa.Column("section", sa.String(length=512), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=True),
        sa.Column("end_char", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rank", sa.Integer(), nullable=False),
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
    op.create_index(
        "ix_chat_message_evidence_message_id", "chat_message_evidence", ["message_id"]
    )
    op.create_index(
        "ix_chat_message_evidence_workspace_id",
        "chat_message_evidence",
        ["workspace_id"],
    )
    op.create_index(
        "ix_chat_message_evidence_paper_id", "chat_message_evidence", ["paper_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_chat_message_evidence_paper_id", table_name="chat_message_evidence")
    op.drop_index("ix_chat_message_evidence_workspace_id", table_name="chat_message_evidence")
    op.drop_index("ix_chat_message_evidence_message_id", table_name="chat_message_evidence")
    op.drop_table("chat_message_evidence")
    op.drop_column("chat_messages", "grounding_status")
    op.drop_index("ix_chat_conversations_workspace_id", table_name="chat_conversations")
    op.drop_column("chat_conversations", "workspace_id")
