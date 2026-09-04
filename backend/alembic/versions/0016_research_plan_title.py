"""为研究计划增加独立的展示标题。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_research_plan_title"
down_revision: str | None = "0015_gap_board"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_plans",
        sa.Column(
            "title",
            sa.Text(),
            nullable=False,
            server_default="未命名研究计划",
        ),
    )
# 从 Opportunity 派生的计划继承简洁、易读的 opportunity 标题。
# Agent 创建的计划和旧版计划回退到缩短后的研究问题。
    op.execute(
        sa.text(
            """
            UPDATE research_plans
            SET title = COALESCE(
                NULLIF((
                    SELECT opportunity_versions.title
                    FROM opportunity_versions
                    WHERE opportunity_versions.id = research_plans.opportunity_version_id
                ), ''),
                NULLIF(SUBSTR(research_plans.research_question, 1, 120), ''),
                '未命名研究计划'
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("research_plans", "title")
