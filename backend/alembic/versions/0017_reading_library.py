"""增加阅读库、阅读进度和论文标注。"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_reading_library"
down_revision: str | None = "0016_research_plan_title"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reading_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="unread"),
        sa.Column("last_read_page", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("paper_id"),
    )
    op.create_index("ix_reading_items_paper_id", "reading_items", ["paper_id"], unique=False)
    op.create_index("ix_reading_items_workspace_id", "reading_items", ["workspace_id"], unique=False)
    op.create_index("ix_reading_items_status", "reading_items", ["status"], unique=False)
    op.create_index("ix_reading_items_is_deleted", "reading_items", ["is_deleted"], unique=False)

    op.create_table(
        "paper_annotations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="note"),
        sa.Column("page_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("selected_text", sa.Text(), nullable=True),
        sa.Column("note_content", sa.Text(), nullable=False),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="#fff1a8"),
        sa.Column("rects", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source_text_hash", sa.String(length=128), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_paper_annotations_paper_id", "paper_annotations", ["paper_id"], unique=False)
    op.create_index("ix_paper_annotations_workspace_id", "paper_annotations", ["workspace_id"], unique=False)
    op.create_index("ix_paper_annotations_artifact_id", "paper_annotations", ["artifact_id"], unique=False)
    op.create_index("ix_paper_annotations_is_deleted", "paper_annotations", ["is_deleted"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_paper_annotations_is_deleted", table_name="paper_annotations")
    op.drop_index("ix_paper_annotations_artifact_id", table_name="paper_annotations")
    op.drop_index("ix_paper_annotations_workspace_id", table_name="paper_annotations")
    op.drop_index("ix_paper_annotations_paper_id", table_name="paper_annotations")
    op.drop_table("paper_annotations")
    op.drop_index("ix_reading_items_is_deleted", table_name="reading_items")
    op.drop_index("ix_reading_items_status", table_name="reading_items")
    op.drop_index("ix_reading_items_workspace_id", table_name="reading_items")
    op.drop_index("ix_reading_items_paper_id", table_name="reading_items")
    op.drop_table("reading_items")
