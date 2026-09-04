"""增加论文解析状态字段：parse_status、parsed_at、chunk_count、parsed_text_artifact_id、chunk_index_artifact_id。

Revision ID：0004_paper_parse_state
Revises：0003_phase1b
创建日期：2026-07-19

Phase 2：Papers 表现在记录 PDF 解析流水线状态。新列使前端无需关联 tasks 表，
即可显示 “parsing...” / “parsed (12 chunks)” / “failed” 标签。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision 标识，供 Alembic 使用。
revision: str = "0004_paper_parse_state"
down_revision: Union[str, None] = "0003_phase1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
# parse_status：默认为 "not_applicable"，使已有论文（可能有或没有 PDF）从安全状态开始。
# upload/attach 流程在附加 PDF 后将其设为 "pending"，并启动 parse_pdf。
    op.add_column(
        "papers",
        sa.Column(
            "parse_status",
            sa.String(32),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column(
        "papers",
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "papers",
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "papers",
        sa.Column(
            "parsed_text_artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "papers",
        sa.Column(
            "chunk_index_artifact_id",
            sa.String(36),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_papers_parse_status", "papers", ["parse_status"])
    op.create_index("ix_papers_parsed_text_artifact_id", "papers", ["parsed_text_artifact_id"])
    op.create_index("ix_papers_chunk_index_artifact_id", "papers", ["chunk_index_artifact_id"])

# 回填：已有 primary_artifact_id 的论文应标记为 "pending"，以便 parse_pdf 任务处理。
# 没有 PDF 的论文保持 "not_applicable"。
    op.execute(
        "UPDATE papers SET parse_status = 'pending' "
        "WHERE primary_artifact_id IS NOT NULL AND is_deleted = false"
    )


def downgrade() -> None:
    op.drop_index("ix_papers_chunk_index_artifact_id", table_name="papers")
    op.drop_index("ix_papers_parsed_text_artifact_id", table_name="papers")
    op.drop_index("ix_papers_parse_status", table_name="papers")
    op.drop_column("papers", "chunk_index_artifact_id")
    op.drop_column("papers", "parsed_text_artifact_id")
    op.drop_column("papers", "chunk_count")
    op.drop_column("papers", "parsed_at")
    op.drop_column("papers", "parse_status")
