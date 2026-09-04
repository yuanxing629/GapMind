"""增加可审计的 Discover Agent 工作流。

本迁移只增加内容。0009 的同步 opportunity 原型保持不变，
新的运行记录逐步填充规范化工作流表。
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.base import UUIDString

revision: str = "0010_discover_runs_opportunity_workflow"
down_revision: Union[str, None] = "0009_mentions_reviews_opportunities"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _common(table: str, columns: list[sa.Column]) -> None:
    op.create_table(table, *columns)


def upgrade() -> None:
    _common(
        "discover_runs",
        [
            sa.Column("id", UUIDString(), primary_key=True, nullable=False),
            sa.Column("workspace_id", UUIDString(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("task_id", UUIDString(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
            sa.Column("parent_run_id", UUIDString(), sa.ForeignKey("discover_runs.id", ondelete="SET NULL"), nullable=True),
            sa.Column("trigger_type", sa.String(32), nullable=False, server_default="topic"),
            sa.Column("input_topic", sa.Text(), nullable=True),
            sa.Column("input_claim_item_id", UUIDString(), sa.ForeignKey("knowledge_items.id", ondelete="SET NULL"), nullable=True),
            sa.Column("input_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("scope", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
            sa.Column("stage", sa.String(48), nullable=False, server_default="preflight"),
            sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
            sa.Column("verification_status", sa.String(32), nullable=False, server_default="not_started"),
            sa.Column("retrieval_snapshot_version", sa.String(32), nullable=False, server_default="v1"),
            sa.Column("prompt_version", sa.String(32), nullable=False, server_default="discover-v1"),
            sa.Column("model_provider", sa.String(64), nullable=True),
            sa.Column("model_name", sa.String(128), nullable=True),
            sa.Column("model_parameters", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("corpus_version", sa.String(64), nullable=False, server_default="workspace-v1"),
            sa.Column("stage_summaries", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("error_code", sa.String(64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        ],
    )
    for name, table, columns in (
        ("ix_discover_runs_workspace_id", "discover_runs", ["workspace_id"]),
        ("ix_discover_runs_task_id", "discover_runs", ["task_id"]),
        ("ix_discover_runs_parent_run_id", "discover_runs", ["parent_run_id"]),
        ("ix_discover_runs_input_claim_item_id", "discover_runs", ["input_claim_item_id"]),
        ("ix_discover_runs_status", "discover_runs", ["status"]),
    ):
        op.create_index(name, table, columns)

    _common(
        "discover_external_candidates",
        [
            sa.Column("id", UUIDString(), primary_key=True, nullable=False),
            sa.Column("discover_run_id", UUIDString(), sa.ForeignKey("discover_runs.id", ondelete="CASCADE"), nullable=False),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("external_paper_id", sa.String(255), nullable=False),
            sa.Column("title", sa.Text(), nullable=False),
            sa.Column("authors", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("year", sa.Integer(), nullable=True),
            sa.Column("abstract", sa.Text(), nullable=True),
            sa.Column("open_access_pdf", sa.JSON(), nullable=True),
            sa.Column("role", sa.String(32), nullable=False, server_default="unknown"),
            sa.Column("role_confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("evidence_level", sa.String(32), nullable=False, server_default="metadata_only"),
            sa.Column("verification_status", sa.String(32), nullable=False, server_default="unverified"),
            sa.Column("imported_paper_id", UUIDString(), sa.ForeignKey("papers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("snapshot_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        ],
    )
    for name, columns in (
        ("ix_discover_external_candidates_run_id", ["discover_run_id"]),
        ("ix_discover_external_candidates_external_paper_id", ["external_paper_id"]),
        ("ix_discover_external_candidates_imported_paper_id", ["imported_paper_id"]),
    ):
        op.create_index(name, "discover_external_candidates", columns)

    _common(
        "opportunity_versions",
        [
            sa.Column("id", UUIDString(), primary_key=True, nullable=False),
            sa.Column("opportunity_id", UUIDString(), sa.ForeignKey("research_opportunities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("version_number", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(512), nullable=False),
            sa.Column("problem_statement", sa.Text(), nullable=False),
            sa.Column("research_scope", sa.Text(), nullable=False, server_default=""),
            sa.Column("why_existing_work_is_insufficient", sa.Text(), nullable=False, server_default=""),
            sa.Column("candidate_research_question", sa.Text(), nullable=False, server_default=""),
            sa.Column("candidate_hypothesis", sa.Text(), nullable=False, server_default=""),
            sa.Column("candidate_validation_plan", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("open_risks", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("novelty_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("feasibility_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("significance_score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("evidence_coverage", sa.Float(), nullable=False, server_default="0"),
            sa.Column("verification_status", sa.String(32), nullable=False, server_default="incomplete"),
            sa.Column("synthesis_metadata", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_by", sa.String(16), nullable=False, server_default="agent"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("opportunity_id", "version_number", name="uq_opportunity_version_number"),
        ],
    )
    op.create_index("ix_opportunity_versions_opportunity_id", "opportunity_versions", ["opportunity_id"])

    op.add_column("research_opportunities", sa.Column("discover_run_id", UUIDString(), sa.ForeignKey("discover_runs.id", ondelete="SET NULL"), nullable=True))
    op.add_column("research_opportunities", sa.Column("current_version_id", UUIDString(), sa.ForeignKey("opportunity_versions.id", ondelete="SET NULL"), nullable=True))
    op.create_index("ix_research_opportunities_discover_run_id", "research_opportunities", ["discover_run_id"])
    op.create_index("ix_research_opportunities_current_version_id", "research_opportunities", ["current_version_id"])

    _common(
        "opportunity_evidence",
        [
            sa.Column("id", UUIDString(), primary_key=True, nullable=False),
            sa.Column("opportunity_version_id", UUIDString(), sa.ForeignKey("opportunity_versions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("relation", sa.String(32), nullable=False),
            sa.Column("source_scope", sa.String(16), nullable=False),
            sa.Column("evidence_level", sa.String(32), nullable=False),
            sa.Column("paper_id", UUIDString(), sa.ForeignKey("papers.id", ondelete="SET NULL"), nullable=True),
            sa.Column("external_candidate_id", UUIDString(), sa.ForeignKey("discover_external_candidates.id", ondelete="SET NULL"), nullable=True),
            sa.Column("evidence_span_id", UUIDString(), sa.ForeignKey("evidence_spans.id", ondelete="SET NULL"), nullable=True),
            sa.Column("artifact_id", UUIDString(), sa.ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True),
            sa.Column("chunk_id", sa.String(255), nullable=True),
            sa.Column("rank", sa.Integer(), nullable=True),
            sa.Column("score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("judgement", sa.String(32), nullable=False, server_default="unknown"),
            sa.Column("judgement_confidence", sa.Float(), nullable=False, server_default="0"),
            sa.Column("display_excerpt", sa.Text(), nullable=False, server_default=""),
            sa.Column("snapshot_payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        ],
    )
    op.create_index("ix_opportunity_evidence_version_id", "opportunity_evidence", ["opportunity_version_id"])
    op.create_index("ix_opportunity_evidence_paper_id", "opportunity_evidence", ["paper_id"])
    op.create_index("ix_opportunity_evidence_external_candidate_id", "opportunity_evidence", ["external_candidate_id"])

    _common(
        "human_decisions",
        [
            sa.Column("id", UUIDString(), primary_key=True, nullable=False),
            sa.Column("opportunity_id", UUIDString(), sa.ForeignKey("research_opportunities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("from_version_id", UUIDString(), sa.ForeignKey("opportunity_versions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("to_version_id", UUIDString(), sa.ForeignKey("opportunity_versions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("action", sa.String(32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("defer_condition", sa.Text(), nullable=True),
            sa.Column("actor", sa.String(64), nullable=False, server_default="user"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        ],
    )
    op.create_index("ix_human_decisions_opportunity_id", "human_decisions", ["opportunity_id"])

    _common(
        "research_plans",
        [
            sa.Column("id", UUIDString(), primary_key=True, nullable=False),
            sa.Column("workspace_id", UUIDString(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
            sa.Column("opportunity_id", UUIDString(), sa.ForeignKey("research_opportunities.id", ondelete="CASCADE"), nullable=False),
            sa.Column("opportunity_version_id", UUIDString(), sa.ForeignKey("opportunity_versions.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
            sa.Column("research_question", sa.Text(), nullable=False),
            sa.Column("hypothesis", sa.Text(), nullable=False),
            sa.Column("scope_and_assumptions", sa.Text(), nullable=False, server_default=""),
            sa.Column("datasets", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("baselines", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("metrics", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("validation_steps", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("expected_supporting_result", sa.Text(), nullable=False, server_default=""),
            sa.Column("falsification_criteria", sa.Text(), nullable=False, server_default=""),
            sa.Column("risks", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("resource_constraints", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        ],
    )
    op.create_index("ix_research_plans_workspace_id", "research_plans", ["workspace_id"])
    op.create_index("ix_research_plans_opportunity_id", "research_plans", ["opportunity_id"])


def downgrade() -> None:
    for name, table in (
        ("ix_research_plans_opportunity_id", "research_plans"),
        ("ix_research_plans_workspace_id", "research_plans"),
        ("ix_human_decisions_opportunity_id", "human_decisions"),
        ("ix_opportunity_evidence_external_candidate_id", "opportunity_evidence"),
        ("ix_opportunity_evidence_paper_id", "opportunity_evidence"),
        ("ix_opportunity_evidence_version_id", "opportunity_evidence"),
        ("ix_opportunity_versions_opportunity_id", "opportunity_versions"),
        ("ix_research_opportunities_current_version_id", "research_opportunities"),
        ("ix_research_opportunities_discover_run_id", "research_opportunities"),
        ("ix_discover_external_candidates_imported_paper_id", "discover_external_candidates"),
        ("ix_discover_external_candidates_external_paper_id", "discover_external_candidates"),
        ("ix_discover_external_candidates_run_id", "discover_external_candidates"),
        ("ix_discover_runs_status", "discover_runs"),
        ("ix_discover_runs_input_claim_item_id", "discover_runs"),
        ("ix_discover_runs_parent_run_id", "discover_runs"),
        ("ix_discover_runs_task_id", "discover_runs"),
        ("ix_discover_runs_workspace_id", "discover_runs"),
    ):
        op.drop_index(name, table_name=table)
    op.drop_table("research_plans")
    op.drop_table("human_decisions")
    op.drop_table("opportunity_evidence")
    op.drop_index("ix_research_opportunities_current_version_id", table_name="research_opportunities")
    op.drop_index("ix_research_opportunities_discover_run_id", table_name="research_opportunities")
    op.drop_column("research_opportunities", "current_version_id")
    op.drop_column("research_opportunities", "discover_run_id")
    op.drop_table("opportunity_versions")
    op.drop_table("discover_external_candidates")
    op.drop_table("discover_runs")
