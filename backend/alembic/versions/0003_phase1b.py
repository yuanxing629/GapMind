"""Create phase 1b tables: artifacts, papers, tasks, timeline_events, knowledge_items, knowledge_relations, evidence_spans.

Revision ID: 0003_phase1b
Revises: 0002_workspaces
Create Date: 2026-07-19

Tables created (in FK dependency order):
  artifacts            - file storage records (pdf, parsed_text, ...)
  papers               - paper metadata + primary_artifact_id FK
  tasks                - async task runtime state machine
  timeline_events      - auto-recorded research activity
  knowledge_items      - the 17-typed research objects (Phase 1b core 7)
  knowledge_relations  - explicit typed edges (the logical KG)
  evidence_spans       - text spans in papers backing knowledge items
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op
from app.db.base import UUIDString

# revision identifiers, used by Alembic.
revision: str = "0003_phase1b"
down_revision: Union[str, None] = "0002_workspaces"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- artifacts ----------------------------------------------------------
    op.create_table(
        "artifacts",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("file_path", sa.String(1024), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=True),
        sa.Column("mime_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_artifacts_workspace_id", "artifacts", ["workspace_id"])
    op.create_index("ix_artifacts_kind", "artifacts", ["kind"])
    op.create_index("ix_artifacts_is_deleted", "artifacts", ["is_deleted"])

    # ---- papers -------------------------------------------------------------
    op.create_table(
        "papers",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "primary_artifact_id",
            UUIDString(),
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("authors", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("doi", sa.String(255), nullable=True),
        sa.Column("arxiv_id", sa.String(64), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("external_paper_id", sa.String(128), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_papers_workspace_id", "papers", ["workspace_id"])
    op.create_index("ix_papers_primary_artifact_id", "papers", ["primary_artifact_id"])
    op.create_index("ix_papers_is_deleted", "papers", ["is_deleted"])

    # ---- tasks --------------------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("celery_task_id", sa.String(128), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_tasks_workspace_id", "tasks", ["workspace_id"])
    op.create_index("ix_tasks_task_type", "tasks", ["task_type"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_celery_task_id", "tasks", ["celery_task_id"])
    op.create_index("ix_tasks_is_deleted", "tasks", ["is_deleted"])

    # ---- timeline_events ----------------------------------------------------
    op.create_table(
        "timeline_events",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(16), nullable=False, server_default="system"),
        sa.Column("subject_type", sa.String(32), nullable=True),
        sa.Column("subject_id", sa.String(36), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_timeline_events_workspace_id", "timeline_events", ["workspace_id"])
    op.create_index("ix_timeline_events_event_type", "timeline_events", ["event_type"])
    op.create_index("ix_timeline_events_subject_type", "timeline_events", ["subject_type"])
    op.create_index("ix_timeline_events_subject_id", "timeline_events", ["subject_id"])

    # ---- knowledge_items ----------------------------------------------------
    op.create_table(
        "knowledge_items",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("source_provenance", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(16), nullable=False, server_default="system"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="extracted_candidate",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_knowledge_items_workspace_id", "knowledge_items", ["workspace_id"])
    op.create_index("ix_knowledge_items_type", "knowledge_items", ["type"])
    op.create_index("ix_knowledge_items_status", "knowledge_items", ["status"])
    op.create_index("ix_knowledge_items_is_deleted", "knowledge_items", ["is_deleted"])

    # ---- knowledge_relations ------------------------------------------------
    op.create_table(
        "knowledge_relations",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            UUIDString(),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            UUIDString(),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_knowledge_relations_workspace_id", "knowledge_relations", ["workspace_id"])
    op.create_index("ix_knowledge_relations_source_id", "knowledge_relations", ["source_id"])
    op.create_index("ix_knowledge_relations_target_id", "knowledge_relations", ["target_id"])
    op.create_index("ix_knowledge_relations_relation_type", "knowledge_relations", ["relation_type"])
    op.create_index("ix_knowledge_relations_is_deleted", "knowledge_relations", ["is_deleted"])

    # ---- evidence_spans -----------------------------------------------------
    op.create_table(
        "evidence_spans",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column(
            "workspace_id",
            UUIDString(),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "knowledge_item_id",
            UUIDString(),
            sa.ForeignKey("knowledge_items.id", ondelete="CASCADE"),
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
            sa.ForeignKey("artifacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("start_char", sa.Integer(), nullable=True),
        sa.Column("end_char", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("relation", sa.String(16), nullable=False, server_default="supports"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_evidence_spans_workspace_id", "evidence_spans", ["workspace_id"])
    op.create_index("ix_evidence_spans_knowledge_item_id", "evidence_spans", ["knowledge_item_id"])
    op.create_index("ix_evidence_spans_paper_id", "evidence_spans", ["paper_id"])


def downgrade() -> None:
    op.drop_index("ix_evidence_spans_paper_id", table_name="evidence_spans")
    op.drop_index("ix_evidence_spans_knowledge_item_id", table_name="evidence_spans")
    op.drop_index("ix_evidence_spans_workspace_id", table_name="evidence_spans")
    op.drop_table("evidence_spans")

    op.drop_index("ix_knowledge_relations_is_deleted", table_name="knowledge_relations")
    op.drop_index("ix_knowledge_relations_relation_type", table_name="knowledge_relations")
    op.drop_index("ix_knowledge_relations_target_id", table_name="knowledge_relations")
    op.drop_index("ix_knowledge_relations_source_id", table_name="knowledge_relations")
    op.drop_index("ix_knowledge_relations_workspace_id", table_name="knowledge_relations")
    op.drop_table("knowledge_relations")

    op.drop_index("ix_knowledge_items_is_deleted", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_status", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_type", table_name="knowledge_items")
    op.drop_index("ix_knowledge_items_workspace_id", table_name="knowledge_items")
    op.drop_table("knowledge_items")

    op.drop_index("ix_timeline_events_subject_id", table_name="timeline_events")
    op.drop_index("ix_timeline_events_subject_type", table_name="timeline_events")
    op.drop_index("ix_timeline_events_event_type", table_name="timeline_events")
    op.drop_index("ix_timeline_events_workspace_id", table_name="timeline_events")
    op.drop_table("timeline_events")

    op.drop_index("ix_tasks_is_deleted", table_name="tasks")
    op.drop_index("ix_tasks_celery_task_id", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_task_type", table_name="tasks")
    op.drop_index("ix_tasks_workspace_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_papers_is_deleted", table_name="papers")
    op.drop_index("ix_papers_primary_artifact_id", table_name="papers")
    op.drop_index("ix_papers_workspace_id", table_name="papers")
    op.drop_table("papers")

    op.drop_index("ix_artifacts_is_deleted", table_name="artifacts")
    op.drop_index("ix_artifacts_kind", table_name="artifacts")
    op.drop_index("ix_artifacts_workspace_id", table_name="artifacts")
    op.drop_table("artifacts")
