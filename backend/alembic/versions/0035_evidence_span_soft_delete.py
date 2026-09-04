"""Add soft deletion to evidence spans used by bounded graph projections."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0035_evidence_span_soft_delete"
down_revision: str | None = "0034_gap_annotation_partial_uniques"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "evidence_spans",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "ix_evidence_spans_is_deleted",
        "evidence_spans",
        ["is_deleted"],
        unique=False,
    )
    op.alter_column("evidence_spans", "is_deleted", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_evidence_spans_is_deleted", table_name="evidence_spans")
    op.drop_column("evidence_spans", "is_deleted")
