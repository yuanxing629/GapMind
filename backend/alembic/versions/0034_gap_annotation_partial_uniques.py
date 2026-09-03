"""Make Gap annotation version uniqueness correct for nullable lineage."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0034_gap_annotation_partial_uniques"
down_revision: str | None = "0033_gap_knowledge_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_paper_gap_annotation_version", "paper_gap_annotations", type_="unique"
    )
    op.create_index(
        "uq_paper_gap_annotation_legacy_version",
        "paper_gap_annotations",
        ["paper_id", "input_sha256", "model_name", "prompt_version", "input_mode"],
        unique=True,
        postgresql_where=sa.text("knowledge_extraction_run_id IS NULL"),
        sqlite_where=sa.text("knowledge_extraction_run_id IS NULL"),
    )
    op.create_index(
        "uq_paper_gap_annotation_knowledge_version",
        "paper_gap_annotations",
        [
            "paper_id",
            "input_sha256",
            "model_name",
            "prompt_version",
            "input_mode",
            "knowledge_extraction_run_id",
        ],
        unique=True,
        postgresql_where=sa.text("knowledge_extraction_run_id IS NOT NULL"),
        sqlite_where=sa.text("knowledge_extraction_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_paper_gap_annotation_knowledge_version",
        table_name="paper_gap_annotations",
    )
    op.drop_index(
        "uq_paper_gap_annotation_legacy_version",
        table_name="paper_gap_annotations",
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
