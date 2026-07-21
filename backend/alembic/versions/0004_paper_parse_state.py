"""Add paper parsing state fields: parse_status, parsed_at, chunk_count, parsed_text_artifact_id, chunk_index_artifact_id.

Revision ID: 0004_paper_parse_state
Revises: 0003_phase1b
Create Date: 2026-07-19

Phase 2: Papers table now tracks the PDF parsing pipeline state. New
columns let the frontend show "parsing..." / "parsed (12 chunks)" /
"failed" badges without needing to join to the tasks table.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_paper_parse_state"
down_revision: Union[str, None] = "0003_phase1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # parse_status: defaults to "not_applicable" so existing papers (which
    # may or may not have a PDF) start in a safe state. The upload/attach
    # flow will set "pending" when a PDF is attached and spawn parse_pdf.
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

    # Backfill: any existing paper with a primary_artifact_id should be
    # marked "pending" so the parse_pdf task can pick it up. Papers without
    # a PDF stay "not_applicable".
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
