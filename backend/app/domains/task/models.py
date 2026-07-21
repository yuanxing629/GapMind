"""Task ORM models.

Task Runtime state machine:
    queued -> running -> waiting_for_user | succeeded | failed
                                    |
                                    v
                          (resumed by user) -> running
    queued | running -> cancel_requested -> cancelled

Phase 1b: only the table + state machine + CRUD. The actual Celery task
wiring (parse_pdf, embed_chunks, extract_knowledge) lands in Phase 2-3.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Task(Base, UUIDPKMixin, TimestampMixin):
    """A long-running task in a workspace."""

    __tablename__ = "tasks"

    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)

    # Progress 0.0 - 1.0
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    # Free-form structured info: input args, current step, partial results.
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Celery integration
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    # Soft delete (admin/cleanup only - tasks normally stay forever for audit)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
