"""Discover Agent 的持久化输出。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


def _now_utc() -> datetime:
    """Python 侧默认值，使 SQLite 测试获得真实时间戳。

    下面的 created_at 列在 PostgreSQL 中仍使用 ``server_default="now()"``；SQLite
    会把 "now()" 当作字面量字符串，无法正确解析，因此在 ORM 侧提供默认值，确保
    INSERT 时写入真实时间戳。
    """
    return datetime.now(timezone.utc)


class ResearchOpportunity(Base, UUIDPKMixin, TimestampMixin):
    """根据 claim 和 evidence 合成的研究方向候选。"""

    __tablename__ = "research_opportunities"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discover_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("discover_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    current_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("opportunity_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    claim_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    suggested_directions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="candidate", nullable=False, index=True)
    source_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)


class DiscoverRun(Base, UUIDPKMixin, TimestampMixin):
    """可审计、可恢复的 Discover Agent 执行记录。"""

    __tablename__ = "discover_runs"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("discover_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trigger_type: Mapped[str] = mapped_column(String(32), default="topic", nullable=False)
    input_topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_claim_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    input_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    scope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="queued", nullable=False, index=True)
    stage: Mapped[str] = mapped_column(String(48), default="preflight", nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(32), default="not_started", nullable=False
    )
    retrieval_snapshot_version: Mapped[str] = mapped_column(
        String(32), default="v1", nullable=False
    )
    prompt_version: Mapped[str] = mapped_column(String(32), default="discover-v1", nullable=False)
    model_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    corpus_version: Mapped[str] = mapped_column(String(64), default="workspace-v1", nullable=False)
    stage_summaries: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[object | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deleted_by: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DiscoverExternalCandidate(Base, UUIDPKMixin, TimestampMixin):
    """某次 run 捕获的近似不可变 Semantic Scholar 快照。"""

    __tablename__ = "discover_external_candidates"

    discover_run_id: Mapped[str] = mapped_column(
        ForeignKey("discover_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    external_paper_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    open_access_pdf: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    role_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_level: Mapped[str] = mapped_column(String(32), default="metadata_only", nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(32), default="unverified", nullable=False
    )
    imported_paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    snapshot_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class OpportunityVersion(Base, UUIDPKMixin):
    """opportunity 的不可变内容版本。"""

    __tablename__ = "opportunity_versions"
    __table_args__ = (
        UniqueConstraint("opportunity_id", "version_number", name="uq_opportunity_version_number"),
    )

    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("research_opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    problem_statement: Mapped[str] = mapped_column(Text, nullable=False)
    research_scope: Mapped[str] = mapped_column(Text, default="", nullable=False)
    why_existing_work_is_insufficient: Mapped[str] = mapped_column(Text, default="", nullable=False)
    candidate_research_question: Mapped[str] = mapped_column(Text, default="", nullable=False)
    candidate_hypothesis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    candidate_validation_plan: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    open_risks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    novelty_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    feasibility_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    significance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    evidence_coverage: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        String(32), default="incomplete", nullable=False
    )
    synthesis_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(16), default="agent", nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default="now()", default=_now_utc, nullable=False)


class OpportunityEvidence(Base, UUIDPKMixin, TimestampMixin):
    """附加到特定不可变 opportunity 版本的证据。"""

    __tablename__ = "opportunity_evidence"

    opportunity_version_id: Mapped[str] = mapped_column(
        ForeignKey("opportunity_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    source_scope: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_level: Mapped[str] = mapped_column(String(32), nullable=False)
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("discover_external_candidates.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    evidence_span_id: Mapped[str | None] = mapped_column(
        ForeignKey("evidence_spans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    chunk_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    judgement: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    judgement_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    display_excerpt: Mapped[str] = mapped_column(Text, default="", nullable=False)
    snapshot_payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class HumanDecision(Base, UUIDPKMixin):
    __tablename__ = "human_decisions"

    opportunity_id: Mapped[str] = mapped_column(
        ForeignKey("research_opportunities.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_version_id: Mapped[str] = mapped_column(
        ForeignKey("opportunity_versions.id", ondelete="RESTRICT"), nullable=False
    )
    to_version_id: Mapped[str] = mapped_column(
        ForeignKey("opportunity_versions.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    defer_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), default="user", nullable=False)
    created_at: Mapped[object] = mapped_column(DateTime(timezone=True), server_default="now()", default=_now_utc, nullable=False)


class ResearchPlan(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "research_plans"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    opportunity_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_opportunities.id", ondelete="CASCADE"), nullable=True, index=True
    )
    opportunity_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("opportunity_versions.id", ondelete="RESTRICT"), nullable=True
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, unique=True
    )
    source_type: Mapped[str] = mapped_column(String(32), default="opportunity", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    title: Mapped[str] = mapped_column(Text, default="未命名研究计划", nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    scope_and_assumptions: Mapped[str] = mapped_column(Text, default="", nullable=False)
    datasets: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    baselines: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metrics: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    validation_steps: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expected_supporting_result: Mapped[str] = mapped_column(Text, default="", nullable=False)
    falsification_criteria: Mapped[str] = mapped_column(Text, default="", nullable=False)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    resource_constraints: Mapped[str] = mapped_column(Text, default="", nullable=False)
