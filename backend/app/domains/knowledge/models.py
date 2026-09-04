"""Knowledge ORM models.

Three tables:
  - knowledge_items       : the 17 typed research objects (Phase 1b core 7)
  - knowledge_relations   : explicit edges between items (the logical KG)
  - evidence_spans        : pointers back into paper text backing each item

Phase 1b: tables + read-only API. Content is written by the extraction
pipeline in Phase 3, not by users directly.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin

# Phase 1b supports the core 7 knowledge types. Others (Opportunity,
# ResearchQuestion, Hypothesis, ResearchPlan, Citation, Note, CodeRepository,
# Baseline, Metric, Idea, FutureWork) arrive in Phase 4-5 as needed.
KNOWLEDGE_TYPES_PHASE_1B = {
    "paper",
    "method",
    "task",
    "dataset",
    "claim",
    "evidence",
    "limitation",
}

# Verification lifecycle (per plans.md):
#   raw_source -> extracted_candidate -> evidence_backed_proposal
#   -> human_confirmed -> experiment_validated -> deprecated | rejected | invalidated
KNOWLEDGE_STATUSES = {
    "raw_source",
    "extracted_candidate",
    "evidence_backed_proposal",
    "human_confirmed",
    "experiment_validated",
    "deprecated",
    "rejected",
    "invalidated",
}

CREATED_BY_VALUES = {"user", "agent", "system"}


class CanonicalEntity(Base, UUIDPKMixin, TimestampMixin):
    """A workspace-scoped identity shared by paper-specific mentions."""

    __tablename__ = "canonical_entities"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "type",
            "normalization_key",
            name="uq_canonical_entity_identity",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalization_key: Mapped[str] = mapped_column(String(512), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="extracted_candidate", nullable=False, index=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )


class PaperMention(Base, UUIDPKMixin, TimestampMixin):
    """A paper-local mention that resolves to a canonical entity.

    Mentions preserve the evidence location used to link a paper to the
    workspace-level entity. They are intentionally separate from
    ``KnowledgeItem`` so one paper can mention the same entity many times.
    """

    __tablename__ = "paper_mentions"
    __table_args__ = (
        UniqueConstraint(
            "paper_id",
            "canonical_entity_id",
            "start_char",
            "end_char",
            name="uq_paper_mention_span",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    canonical_entity_id: Mapped[str] = mapped_column(
        ForeignKey("canonical_entities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    knowledge_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    mention_text: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="extracted_candidate", nullable=False, index=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )


class ExtractionRun(Base, UUIDPKMixin, TimestampMixin):
    """One versioned extraction attempt for one immutable source artifact."""

    __tablename__ = "extraction_runs"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_extraction_runs_task_id"),
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
    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    schema_version: Mapped[str] = mapped_column(
        String(32), default="1.0.0", nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="running", nullable=False, index=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ExtractionRejection(Base, UUIDPKMixin, TimestampMixin):
    """One rejected LLM item/relation retained for quality audit."""

    __tablename__ = "extraction_rejections"
    __table_args__ = (
        UniqueConstraint(
            "extraction_run_id",
            "fingerprint",
            name="uq_extraction_rejection_run_fingerprint",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extraction_run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rejection_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason_detail: Mapped[str] = mapped_column(Text, nullable=False)
    item_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    canonical_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )


class KnowledgeItem(Base, UUIDPKMixin, TimestampMixin):
    """A single knowledge object in a workspace.

    `content` is a JSON blob whose shape depends on `type` - e.g. a Method
    might carry {name, description, inputs, outputs}, a Claim might carry
    {statement, scope, conditions}. The shape is enforced at the service
    layer (Phase 3) rather than by the DB.
    """

    __tablename__ = "knowledge_items"
    __table_args__ = (
        UniqueConstraint(
            "extraction_run_id",
            "item_key",
            name="uq_knowledge_item_run_key",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=True, index=True
    )
    canonical_entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("canonical_entities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    extraction_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_runs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    item_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Provenance
    source_provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), default="system", nullable=False)

    # Lifecycle
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="extracted_candidate", nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )


class KnowledgeRelation(Base, UUIDPKMixin, TimestampMixin):
    """An explicit typed edge between two KnowledgeItems.

    Relation types (per plans.md): proposes, addresses, evaluates_on,
    compares_with, claims, mentions_limitation, suggests, extends,
    supports, qualifies, contradicts, derived_from, related_to.
    """

    __tablename__ = "knowledge_relations"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )


class EvidenceSpan(Base, UUIDPKMixin, TimestampMixin):
    """A pointer to a span of text in a paper that backs a KnowledgeItem.

    Phase 1b stores chunk_index + char offsets. Phase 2 will define the
    chunk shape; Phase 3 will populate these rows during extraction.
    """

    __tablename__ = "evidence_spans"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_item_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    artifact_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    artifact_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    relation: Mapped[str] = mapped_column(
        String(16), default="supports", nullable=False
    )  # supports | qualifies | contradicts
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Evidence is a first-class provenance object.  Keep soft deletion here so
    # graph projections cannot resurrect an invalidated span.
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
