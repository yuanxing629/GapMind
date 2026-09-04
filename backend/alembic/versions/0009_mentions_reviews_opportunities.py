"""增加论文提及、人工审核元数据和研究机会。

Revision ID：0009_mentions_reviews_opportunities
Revises：0008_search_history_favorites
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from app.db.base import UUIDString

revision: str = "0009_mentions_reviews_opportunities"
down_revision: Union[str, None] = "0008_search_history_favorites"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
# Alembic 默认将 version_num 创建为 VARCHAR(32)，但此迁移标识超过 32 个字符。
    op.execute(
        "ALTER TABLE alembic_version "
        "ALTER COLUMN version_num TYPE VARCHAR(64)"
    )

    op.create_table(
        "paper_mentions",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("workspace_id", UUIDString(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", UUIDString(), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("canonical_entity_id", UUIDString(), sa.ForeignKey("canonical_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("knowledge_item_id", UUIDString(), sa.ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("mention_text", sa.Text(), nullable=False),
        sa.Column("artifact_id", UUIDString(), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("start_char", sa.Integer(), nullable=True),
        sa.Column("end_char", sa.Integer(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="extracted_candidate"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("paper_id", "canonical_entity_id", "start_char", "end_char", name="uq_paper_mention_span"),
    )
    for name, cols in {
        "ix_paper_mentions_workspace_id": ["workspace_id"],
        "ix_paper_mentions_paper_id": ["paper_id"],
        "ix_paper_mentions_canonical_entity_id": ["canonical_entity_id"],
        "ix_paper_mentions_knowledge_item_id": ["knowledge_item_id"],
        "ix_paper_mentions_status": ["status"],
        "ix_paper_mentions_is_deleted": ["is_deleted"],
    }.items():
        op.create_index(name, "paper_mentions", cols)

    op.add_column("knowledge_items", sa.Column("reviewed_by", sa.String(64), nullable=True))
    op.add_column("knowledge_items", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("knowledge_items", sa.Column("review_note", sa.Text(), nullable=True))

    op.create_table(
        "research_opportunities",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("workspace_id", UUIDString(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("claim_item_id", UUIDString(), sa.ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("suggested_directions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(32), nullable=False, server_default="candidate"),
        sa.Column("source_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_research_opportunities_workspace_id", "research_opportunities", ["workspace_id"])
    op.create_index("ix_research_opportunities_claim_item_id", "research_opportunities", ["claim_item_id"])
    op.create_index("ix_research_opportunities_status", "research_opportunities", ["status"])
    op.create_index("ix_research_opportunities_is_deleted", "research_opportunities", ["is_deleted"])


def downgrade() -> None:
    for name in (
        "ix_research_opportunities_is_deleted",
        "ix_research_opportunities_status",
        "ix_research_opportunities_claim_item_id",
        "ix_research_opportunities_workspace_id",
    ):
        op.drop_index(name, table_name="research_opportunities")
    op.drop_table("research_opportunities")
    op.drop_column("knowledge_items", "review_note")
    op.drop_column("knowledge_items", "reviewed_at")
    op.drop_column("knowledge_items", "reviewed_by")
    for name in (
        "ix_paper_mentions_is_deleted",
        "ix_paper_mentions_status",
        "ix_paper_mentions_knowledge_item_id",
        "ix_paper_mentions_canonical_entity_id",
        "ix_paper_mentions_paper_id",
        "ix_paper_mentions_workspace_id",
    ):
        op.drop_index(name, table_name="paper_mentions")
    op.drop_table("paper_mentions")
