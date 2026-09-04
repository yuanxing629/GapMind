"""增加微调 Gap 标注、规范概念和棋盘快照。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.base import UUIDString

revision: str = "0015_gap_board"
down_revision: str | None = "0014_workspace_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "paper_gap_annotations",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("workspace_id", UUIDString(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("paper_id", UUIDString(), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_id", UUIDString(), sa.ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_id", UUIDString(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False, server_default="3.0"),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model_provider", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(256), nullable=False),
        sa.Column("model_digest", sa.String(128), nullable=True),
        sa.Column("model_parameters", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("raw_responses", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("validation_errors", sa.JSON(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.UniqueConstraint("paper_id", "input_sha256", "model_name", "prompt_version", name="uq_paper_gap_annotation_version"),
    )
    for column in ("workspace_id", "paper_id", "artifact_id", "task_id", "input_sha256", "status", "is_deleted"):
        op.create_index(f"ix_paper_gap_annotations_{column}", "paper_gap_annotations", [column])

    op.create_table(
        "gap_canonical_concepts",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("workspace_id", UUIDString(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("axis_type", sa.String(16), nullable=False),
        sa.Column("canonical_label", sa.Text(), nullable=False),
        sa.Column("normalization_key", sa.String(512), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="auto_exact"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "axis_type", "normalization_key", name="uq_gap_concept_identity"),
    )
    for column in ("workspace_id", "axis_type", "is_deleted"):
        op.create_index(f"ix_gap_canonical_concepts_{column}", "gap_canonical_concepts", [column])

    op.create_table(
        "gap_concept_assignments",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("annotation_id", UUIDString(), sa.ForeignKey("paper_gap_annotations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("concept_id", UUIDString(), sa.ForeignKey("gap_canonical_concepts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("axis_type", sa.String(16), nullable=False),
        sa.Column("local_entity_id", sa.String(32), nullable=False),
        sa.Column("original_label", sa.Text(), nullable=False),
        sa.Column("mapping_method", sa.String(32), nullable=False, server_default="exact"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint("annotation_id", "axis_type", "local_entity_id", name="uq_gap_assignment_local"),
    )
    for column in ("annotation_id", "concept_id", "axis_type"):
        op.create_index(f"ix_gap_concept_assignments_{column}", "gap_concept_assignments", [column])

    op.create_table(
        "gap_board_snapshots",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("workspace_id", UUIDString(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("method_axes", sa.JSON(), nullable=False),
        sa.Column("problem_axes", sa.JSON(), nullable=False),
        sa.Column("cells", sa.JSON(), nullable=False),
        sa.Column("source_annotation_ids", sa.JSON(), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.UniqueConstraint("workspace_id", "version", name="uq_gap_board_workspace_version"),
    )
    op.create_index("ix_gap_board_snapshots_workspace_id", "gap_board_snapshots", ["workspace_id"])
    op.create_index("ix_gap_board_snapshots_is_deleted", "gap_board_snapshots", ["is_deleted"])


def downgrade() -> None:
    op.drop_table("gap_board_snapshots")
    op.drop_table("gap_concept_assignments")
    op.drop_table("gap_canonical_concepts")
    op.drop_table("paper_gap_annotations")
