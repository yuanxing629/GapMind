"""为 Discover 运行记录增加软删除元数据。"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0012_discover_run_soft_delete"
down_revision: Union[str, None] = "0011_chat_conversations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("discover_runs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("discover_runs", sa.Column("deleted_by", sa.String(length=64), nullable=True))
    op.create_index("ix_discover_runs_deleted_at", "discover_runs", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_discover_runs_deleted_at", table_name="discover_runs")
    op.drop_column("discover_runs", "deleted_by")
    op.drop_column("discover_runs", "deleted_at")
