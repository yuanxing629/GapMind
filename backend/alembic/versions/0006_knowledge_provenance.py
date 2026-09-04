"""增加规范实体和版本化抽取来源追溯。

Revision ID：0006_knowledge_provenance
Revises：0005_paper_markdown
创建日期：2026-07-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from app.db.base import UUIDString

revision: str = "0006_knowledge_provenance"
down_revision: Union[str, None] = "0005_paper_markdown"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "canonical_entities",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("normalization_key", sa.String(512), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="extracted_candidate",
        ),
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
            "workspace_id",
            "type",
            "normalization_key",
            name="uq_canonical_entity_identity",
        ),
    )
    op.create_index(
        "ix_canonical_entities_workspace_id",
        "canonical_entities",
        ["workspace_id"],
    )
    op.create_index("ix_canonical_entities_type", "canonical_entities", ["type"])
    op.create_index(
        "ix_canonical_entities_status", "canonical_entities", ["status"]
    )
    op.create_index(
        "ix_canonical_entities_is_deleted", "canonical_entities", ["is_deleted"]
    )

    op.create_table(
        "extraction_runs",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "paper_id",
            UUIDString(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "artifact_id",
            UUIDString(),
            sa.ForeignKey("artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            UUIDString(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("model_provider", sa.String(64), nullable=False),
        sa.Column("model_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("task_id", name="uq_extraction_runs_task_id"),
    )
    op.create_index(
        "ix_extraction_runs_workspace_id", "extraction_runs", ["workspace_id"]
    )
    op.create_index("ix_extraction_runs_paper_id", "extraction_runs", ["paper_id"])
    op.create_index(
        "ix_extraction_runs_artifact_id", "extraction_runs", ["artifact_id"]
    )
    op.create_index("ix_extraction_runs_task_id", "extraction_runs", ["task_id"])
    op.create_index("ix_extraction_runs_status", "extraction_runs", ["status"])

    op.add_column(
        "knowledge_items",
        sa.Column(
            "paper_id",
            UUIDString(),
            sa.ForeignKey("papers.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_items",
        sa.Column(
            "canonical_entity_id",
            UUIDString(),
            sa.ForeignKey("canonical_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_items",
        sa.Column(
            "extraction_run_id",
            UUIDString(),
            sa.ForeignKey("extraction_runs.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "knowledge_items", sa.Column("item_key", sa.String(128), nullable=True)
    )
    op.create_index("ix_knowledge_items_paper_id", "knowledge_items", ["paper_id"])
    op.create_index(
        "ix_knowledge_items_canonical_entity_id",
        "knowledge_items",
        ["canonical_entity_id"],
    )
    op.create_index(
        "ix_knowledge_items_extraction_run_id",
        "knowledge_items",
        ["extraction_run_id"],
    )
    op.create_unique_constraint(
        "uq_knowledge_item_run_key",
        "knowledge_items",
        ["extraction_run_id", "item_key"],
    )

    op.add_column(
        "evidence_spans",
        sa.Column("artifact_kind", sa.String(32), nullable=True),
    )
    op.add_column(
        "evidence_spans",
        sa.Column("artifact_version", sa.String(32), nullable=True),
    )

    op.add_column(
        "papers",
        sa.Column(
            "extract_status",
            sa.String(32),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column(
        "papers",
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_papers_extract_status", "papers", ["extract_status"])
    op.execute(
        "UPDATE papers SET extract_status = 'pending' "
        "WHERE parsed_markdown_artifact_id IS NOT NULL AND is_deleted = false"
    )


def downgrade() -> None:
    op.drop_index("ix_papers_extract_status", table_name="papers")
    op.drop_column("papers", "extracted_at")
    op.drop_column("papers", "extract_status")

    op.drop_column("evidence_spans", "artifact_version")
    op.drop_column("evidence_spans", "artifact_kind")

    op.drop_constraint(
        "uq_knowledge_item_run_key", "knowledge_items", type_="unique"
    )
    op.drop_index(
        "ix_knowledge_items_extraction_run_id", table_name="knowledge_items"
    )
    op.drop_index(
        "ix_knowledge_items_canonical_entity_id", table_name="knowledge_items"
    )
    op.drop_index("ix_knowledge_items_paper_id", table_name="knowledge_items")
    op.drop_column("knowledge_items", "item_key")
    op.drop_column("knowledge_items", "extraction_run_id")
    op.drop_column("knowledge_items", "canonical_entity_id")
    op.drop_column("knowledge_items", "paper_id")

    op.drop_index("ix_extraction_runs_status", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_task_id", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_artifact_id", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_paper_id", table_name="extraction_runs")
    op.drop_index("ix_extraction_runs_workspace_id", table_name="extraction_runs")
    op.drop_table("extraction_runs")

    op.drop_index(
        "ix_canonical_entities_is_deleted", table_name="canonical_entities"
    )
    op.drop_index("ix_canonical_entities_status", table_name="canonical_entities")
    op.drop_index("ix_canonical_entities_type", table_name="canonical_entities")
    op.drop_index(
        "ix_canonical_entities_workspace_id", table_name="canonical_entities"
    )
    op.drop_table("canonical_entities")
