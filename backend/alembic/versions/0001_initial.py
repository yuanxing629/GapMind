"""初始 schema（空表 - Phase 0 基线）。

Revision ID：0001_initial
Revises：
创建日期：2026-07-18

本迁移建立 Alembic 基线。当前尚未创建任何表；领域表将在后续从 Phase 1 开始的迁移中增加。
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


    # revision 标识，供 Alembic 使用。
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 0 基线 - 当前没有表。
    pass


def downgrade() -> None:
    pass
