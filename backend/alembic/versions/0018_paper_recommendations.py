"""增加缓存的工作区论文推荐。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_paper_recommendations"
down_revision: str | None = "0017_reading_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "paper_recommendations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("external_paper_id", sa.String(length=255), nullable=False),
        sa.Column("paper", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("topics", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="suggested"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "external_paper_id", name="uq_paper_recommendations_workspace_external"
        ),
    )
    op.create_index("ix_paper_recommendations_workspace_id", "paper_recommendations", ["workspace_id"])
    op.create_index("ix_paper_recommendations_external_paper_id", "paper_recommendations", ["external_paper_id"])
    op.create_index("ix_paper_recommendations_status", "paper_recommendations", ["status"])
    op.create_index("ix_paper_recommendations_is_active", "paper_recommendations", ["is_active"])
    op.create_index("ix_paper_recommendations_generated_at", "paper_recommendations", ["generated_at"])


def downgrade() -> None:
    op.drop_index("ix_paper_recommendations_generated_at", table_name="paper_recommendations")
    op.drop_index("ix_paper_recommendations_is_active", table_name="paper_recommendations")
    op.drop_index("ix_paper_recommendations_status", table_name="paper_recommendations")
    op.drop_index("ix_paper_recommendations_external_paper_id", table_name="paper_recommendations")
    op.drop_index("ix_paper_recommendations_workspace_id", table_name="paper_recommendations")
    op.drop_table("paper_recommendations")
