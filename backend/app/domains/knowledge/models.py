"""Knowledge ORM 模型。

三张表：
  - knowledge_items      ：17 种有类型的研究对象（Phase 1b 核心 7 种）
  - knowledge_relations  ：项之间的显式边（逻辑 KG）
  - evidence_spans       ：指回支撑每个项的论文文本

Phase 1b：表和只读 API。内容由 Phase 3 抽取流水线写入，而不是由用户直接写入。
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

# Phase 1b 支持核心的 7 种知识类型。其他类型（Opportunity、ResearchQuestion、
# Hypothesis、ResearchPlan、Citation、Note、CodeRepository、Baseline、Metric、
# Idea、FutureWork）按需在 Phase 4-5 引入。
KNOWLEDGE_TYPES_PHASE_1B = {
    "paper",
    "method",
    "task",
    "dataset",
    "claim",
    "evidence",
    "limitation",
}

# 校验生命周期（依据 plans.md）：
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
    """按 workspace 限定、由论文级 mention 共享的身份。"""

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
    """解析到 canonical entity 的论文级 mention。

    Mention 保留将论文链接到 workspace 级实体时使用的证据位置。它们有意与
    ``KnowledgeItem`` 分离，因此一篇论文可以多次提及同一实体。
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
    """针对一个不可变源 artifact 的一次版本化抽取尝试。"""

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
    """为质量审计保留的一条被拒绝 LLM 条目/关系。"""

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
    """workspace 中的一个知识对象。

    `content` 是形状取决于 `type` 的 JSON blob。例如 Method 可能包含
    {name, description, inputs, outputs}，Claim 可能包含 {statement, scope, conditions}。
    其结构由 service 层（Phase 3）而非 DB 强制校验。
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

# 来源追溯（Provenance）
    source_provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), default="system", nullable=False)

# 生命周期
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
    """两个 KnowledgeItem 之间的显式类型化边。

    Relation types（依据 plans.md）：proposes、addresses、evaluates_on、
    compares_with、claims、mentions_limitation、suggests、extends、supports、
    qualifies、contradicts、derived_from、related_to。
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
    """指向论文文本范围的指针，用于支持 KnowledgeItem。

    Phase 1b 保存 chunk_index 和字符偏移。Phase 2 定义 chunk 结构；Phase 3 在抽取过程
    中填充这些行。
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
    # Evidence 是一等 provenance 对象。在此保留软删除，避免 graph 投影重新
    # 引用已经失效的 span。
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
