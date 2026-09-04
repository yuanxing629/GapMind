"""增加受控工作区 Agent、步骤、Artifact 和来源于 Chat 的计划。"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.db.base import UUIDString


revision: str = "0014_workspace_agents"
down_revision: Union[str, None] = "0013_workspace_grounded_chat"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    ]


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("workspace_id", UUIDString(), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", UUIDString(), sa.ForeignKey("chat_conversations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("trigger_message_id", UUIDString(), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("assistant_message_id", UUIDString(), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", UUIDString(), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("parent_run_id", UUIDString(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("agent_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("current_stage", sa.String(64), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("context_snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requires_confirmation", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    op.create_index("ix_agent_runs_workspace_id", "agent_runs", ["workspace_id"])
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])
    op.create_index("ix_agent_runs_task_id", "agent_runs", ["task_id"])
    op.create_index("ix_agent_runs_agent_type", "agent_runs", ["agent_type"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_workspace_status", "agent_runs", ["workspace_id", "status"])
    op.create_index("ix_agent_runs_conversation_created", "agent_runs", ["conversation_id", "created_at"])
    op.create_table(
        "agent_steps",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("run_id", UUIDString(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_step_sequence"),
    )
    op.create_index("ix_agent_steps_run_id", "agent_steps", ["run_id"])
    op.create_table(
        "agent_artifacts",
        sa.Column("id", UUIDString(), primary_key=True, nullable=False),
        sa.Column("run_id", UUIDString(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artifact_type", sa.String(64), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False, server_default="text/plain"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("validation_status", sa.String(32), nullable=False, server_default="unreviewed"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    op.create_index("ix_agent_artifacts_run_id", "agent_artifacts", ["run_id"])
    op.create_index("ix_agent_artifacts_is_deleted", "agent_artifacts", ["is_deleted"])
    op.create_index("ix_agent_artifacts_run_type", "agent_artifacts", ["run_id", "artifact_type"])
    op.alter_column("research_plans", "opportunity_id", existing_type=UUIDString(), nullable=True)
    op.alter_column("research_plans", "opportunity_version_id", existing_type=UUIDString(), nullable=True)
    op.add_column("research_plans", sa.Column("agent_run_id", UUIDString(), sa.ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True))
    op.add_column("research_plans", sa.Column("source_type", sa.String(32), nullable=False, server_default="opportunity"))
    op.create_unique_constraint("uq_research_plans_agent_run_id", "research_plans", ["agent_run_id"])


def downgrade() -> None:
    op.drop_constraint("uq_research_plans_agent_run_id", "research_plans", type_="unique")
    op.drop_column("research_plans", "source_type")
    op.drop_column("research_plans", "agent_run_id")
    op.alter_column("research_plans", "opportunity_version_id", existing_type=UUIDString(), nullable=False)
    op.alter_column("research_plans", "opportunity_id", existing_type=UUIDString(), nullable=False)
    op.drop_index("ix_agent_artifacts_run_type", table_name="agent_artifacts")
    op.drop_index("ix_agent_artifacts_is_deleted", table_name="agent_artifacts")
    op.drop_index("ix_agent_artifacts_run_id", table_name="agent_artifacts")
    op.drop_table("agent_artifacts")
    op.drop_index("ix_agent_steps_run_id", table_name="agent_steps")
    op.drop_table("agent_steps")
    op.drop_index("ix_agent_runs_conversation_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_workspace_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_agent_type", table_name="agent_runs")
    op.drop_index("ix_agent_runs_task_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_conversation_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_workspace_id", table_name="agent_runs")
    op.drop_table("agent_runs")

