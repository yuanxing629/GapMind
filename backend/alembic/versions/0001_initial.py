"""Initial schema (empty - Phase 0 baseline).

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-18

This migration establishes the Alembic baseline. No tables are created yet;
domain tables will be added in subsequent migrations starting from Phase 1.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Phase 0 baseline - no tables yet.
    pass


def downgrade() -> None:
    pass
