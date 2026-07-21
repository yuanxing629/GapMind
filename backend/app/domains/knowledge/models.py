"""Knowledge ORM models.

Three tables:
  - knowledge_items       : the 17 typed research objects (Phase 1b core 7)
  - knowledge_relations   : explicit edges between items (the logical KG)
  - evidence_spans        : pointers back into paper text backing each item

Phase 1b: tables + read-only API. Content is written by the extraction
pipeline in Phase 3, not by users directly.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
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


class KnowledgeItem(Base, UUIDPKMixin, TimestampMixin):
    """A single knowledge object in a workspace.

    `content` is a JSON blob whose shape depends on `type` - e.g. a Method
    might carry {name, description, inputs, outputs}, a Claim might carry
    {statement, scope, conditions}. The shape is enforced at the service
    layer (Phase 3) rather than by the DB.
    """

    __tablename__ = "knowledge_items"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
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
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    relation: Mapped[str] = mapped_column(
        String(16), default="supports", nullable=False
    )  # supports | qualifies | contradicts
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
