"""增加抽取拒绝审计记录。

Revision ID：0007_extraction_rejections
Revises：0006_knowledge_provenance
创建日期：2026-07-26
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from app.db.base import UUIDString

revision: str = "0007_extraction_rejections"
down_revision: Union[str, None] = "0006_knowledge_provenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "extraction_rejections",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "extraction_run_id",
            UUIDString(),
            sa.ForeignKey("extraction_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_id",
            UUIDString(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_index", sa.Integer(), nullable=True),
        sa.Column("rejection_kind", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=False),
        sa.Column("item_type", sa.String(32), nullable=True),
        sa.Column("canonical_name", sa.Text(), nullable=True),
        sa.Column("raw_payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("evidence_preview", sa.Text(), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
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
        sa.UniqueConstraint(
            "extraction_run_id",
            "fingerprint",
            name="uq_extraction_rejection_run_fingerprint",
        ),
    )
    op.create_index(
        "ix_extraction_rejections_workspace_id",
        "extraction_rejections",
        ["workspace_id"],
    )
    op.create_index(
        "ix_extraction_rejections_extraction_run_id",
        "extraction_rejections",
        ["extraction_run_id"],
    )
    op.create_index(
        "ix_extraction_rejections_paper_id",
        "extraction_rejections",
        ["paper_id"],
    )
    op.create_index(
        "ix_extraction_rejections_rejection_kind",
        "extraction_rejections",
        ["rejection_kind"],
    )
    op.create_index(
        "ix_extraction_rejections_stage",
        "extraction_rejections",
        ["stage"],
    )
    op.create_index(
        "ix_extraction_rejections_reason_code",
        "extraction_rejections",
        ["reason_code"],
    )
    op.create_index(
        "ix_extraction_rejections_is_deleted",
        "extraction_rejections",
        ["is_deleted"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_rejections_is_deleted", table_name="extraction_rejections"
    )
    op.drop_index(
        "ix_extraction_rejections_reason_code", table_name="extraction_rejections"
    )
    op.drop_index(
        "ix_extraction_rejections_stage", table_name="extraction_rejections"
    )
    op.drop_index(
        "ix_extraction_rejections_rejection_kind",
        table_name="extraction_rejections",
    )
    op.drop_index(
        "ix_extraction_rejections_paper_id", table_name="extraction_rejections"
    )
    op.drop_index(
        "ix_extraction_rejections_extraction_run_id",
        table_name="extraction_rejections",
    )
    op.drop_index(
        "ix_extraction_rejections_workspace_id",
        table_name="extraction_rejections",
    )
    op.drop_table("extraction_rejections")
