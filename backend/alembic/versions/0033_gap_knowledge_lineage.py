"""记录专用 Gap 抽取的知识上下文 lineage。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0033_gap_knowledge_lineage"
down_revision: str | None = "0032_widen_timeline_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "paper_gap_annotations",
        sa.Column(
            "knowledge_extraction_run_id",
            sa.String(length=36),
            sa.ForeignKey("extraction_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "paper_gap_annotations",
        sa.Column("knowledge_context_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "paper_gap_annotations",
        sa.Column(
            "input_mode",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'core_markdown_legacy_v1'"),
        ),
    )
    op.add_column(
        "paper_gap_annotations",
        sa.Column("source_knowledge_item_ids", sa.JSON(), nullable=True),
    )
    op.add_column(
        "paper_gap_annotations",
        sa.Column("source_evidence_span_ids", sa.JSON(), nullable=True),
    )
    op.add_column(
        "paper_gap_annotations",
        sa.Column("context_char_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "paper_gap_annotations",
        sa.Column("context_fallback_reason", sa.String(length=128), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE paper_gap_annotations "
            "SET source_knowledge_item_ids = '[]' "
            "WHERE source_knowledge_item_ids IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE paper_gap_annotations "
            "SET source_evidence_span_ids = '[]' "
            "WHERE source_evidence_span_ids IS NULL"
        )
    )
    op.alter_column(
        "paper_gap_annotations",
        "source_knowledge_item_ids",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.alter_column(
        "paper_gap_annotations",
        "source_evidence_span_ids",
        existing_type=sa.JSON(),
        nullable=False,
    )
    op.create_index(
        "ix_paper_gap_annotations_knowledge_extraction_run_id",
        "paper_gap_annotations",
        ["knowledge_extraction_run_id"],
    )
    op.create_index(
        "ix_paper_gap_annotations_knowledge_context_sha256",
        "paper_gap_annotations",
        ["knowledge_context_sha256"],
    )
    op.drop_constraint(
        "uq_paper_gap_annotation_version", "paper_gap_annotations", type_="unique"
    )
    op.create_unique_constraint(
        "uq_paper_gap_annotation_version",
        "paper_gap_annotations",
        [
            "paper_id",
            "input_sha256",
            "model_name",
            "prompt_version",
            "input_mode",
            "knowledge_extraction_run_id",
        ],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_paper_gap_annotation_version", "paper_gap_annotations", type_="unique"
    )
    op.create_unique_constraint(
        "uq_paper_gap_annotation_version",
        "paper_gap_annotations",
        [
            "paper_id",
            "input_sha256",
            "model_name",
            "prompt_version",
        ],
    )
    op.drop_index(
        "ix_paper_gap_annotations_knowledge_context_sha256",
        table_name="paper_gap_annotations",
    )
    op.drop_index(
        "ix_paper_gap_annotations_knowledge_extraction_run_id",
        table_name="paper_gap_annotations",
    )
    op.drop_column("paper_gap_annotations", "context_fallback_reason")
    op.drop_column("paper_gap_annotations", "context_char_count")
    op.drop_column("paper_gap_annotations", "source_evidence_span_ids")
    op.drop_column("paper_gap_annotations", "source_knowledge_item_ids")
    op.drop_column("paper_gap_annotations", "input_mode")
    op.drop_column("paper_gap_annotations", "knowledge_context_sha256")
    op.drop_column("paper_gap_annotations", "knowledge_extraction_run_id")
