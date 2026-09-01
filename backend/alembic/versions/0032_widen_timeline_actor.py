"""Allow timeline events to record authenticated user identities."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0032_widen_timeline_actor"
down_revision: str | None = "0031_chat_message_images"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "timeline_events",
        "actor",
        existing_type=sa.String(length=16),
        type_=sa.String(length=128),
        existing_nullable=False,
        existing_server_default=sa.text("'system'::character varying"),
    )


def downgrade() -> None:
    # A downgrade is only safe when no authenticated identity longer than the
    # legacy 16-character actor label remains in the table.
    op.alter_column(
        "timeline_events",
        "actor",
        existing_type=sa.String(length=128),
        type_=sa.String(length=16),
        existing_nullable=False,
        existing_server_default=sa.text("'system'::character varying"),
    )
