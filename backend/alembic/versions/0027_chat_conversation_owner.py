"""将 Chat 会话限定到已认证所有者。

Revision ID：0027_chat_conversation_owner
Revises：0026_paper_parse_quality
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_chat_conversation_owner"
down_revision = "0026_paper_parse_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_conversations",
        sa.Column("owner_id", sa.String(length=128), nullable=False, server_default="user"),
    )
    op.create_index(
        "ix_chat_conversations_owner_id",
        "chat_conversations",
        ["owner_id"],
    )
    op.alter_column("chat_conversations", "owner_id", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_chat_conversations_owner_id", table_name="chat_conversations")
    op.drop_column("chat_conversations", "owner_id")
