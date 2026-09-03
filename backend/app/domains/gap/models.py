"""Persistence models for specialized gap extraction and board snapshots."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class PaperGapAnnotation(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "paper_gap_annotations"
    __table_args__ = (
        Index(
            "uq_paper_gap_annotation_legacy_version",
            "paper_id",
            "input_sha256",
            "model_name",
            "prompt_version",
            "input_mode",
            unique=True,
            postgresql_where=text("knowledge_extraction_run_id IS NULL"),
            sqlite_where=text("knowledge_extraction_run_id IS NULL"),
        ),
        Index(
            "uq_paper_gap_annotation_knowledge_version",
            "paper_id",
            "input_sha256",
            "model_name",
            "prompt_version",
            "input_mode",
            "knowledge_extraction_run_id",
            unique=True,
            postgresql_where=text("knowledge_extraction_run_id IS NOT NULL"),
            sqlite_where=text("knowledge_extraction_run_id IS NOT NULL"),
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    knowledge_extraction_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    knowledge_context_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    input_mode: Mapped[str] = mapped_column(
        String(64), default="core_markdown_legacy_v1", nullable=False
    )
    source_knowledge_item_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_evidence_span_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    context_char_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    context_fallback_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(16), default="3.0", nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    model_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    raw_responses: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_errors: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    fallback_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)


class GapCanonicalConcept(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "gap_canonical_concepts"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "axis_type", "normalization_key", name="uq_gap_concept_identity"
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    axis_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    canonical_label: Mapped[str] = mapped_column(Text, nullable=False)
    normalization_key: Mapped[str] = mapped_column(String(512), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="auto_exact", nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)


class GapConceptAssignment(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "gap_concept_assignments"
    __table_args__ = (
        UniqueConstraint(
            "annotation_id", "axis_type", "local_entity_id", name="uq_gap_assignment_local"
        ),
    )

    annotation_id: Mapped[str] = mapped_column(
        ForeignKey("paper_gap_annotations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    concept_id: Mapped[str] = mapped_column(
        ForeignKey("gap_canonical_concepts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    axis_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    local_entity_id: Mapped[str] = mapped_column(String(32), nullable=False)
    original_label: Mapped[str] = mapped_column(Text, nullable=False)
    mapping_method: Mapped[str] = mapped_column(String(32), default="exact", nullable=False)
    confidence: Mapped[float] = mapped_column(default=1.0, nullable=False)


class GapBoardSnapshot(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "gap_board_snapshots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "version", name="uq_gap_board_workspace_version"),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    filters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    method_axes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    problem_axes: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    cells: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_annotation_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)

