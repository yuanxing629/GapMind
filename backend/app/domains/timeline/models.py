"""Timeline ORM models.

TimelineEvent is auto-recorded by the system whenever a meaningful research
action happens - workspace created, paper uploaded, task transitioned, etc.
Users never write Timeline entries directly.
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class TimelineEvent(Base, UUIDPKMixin, TimestampMixin):
    """A single auto-recorded research activity event.

    `subject_type` + `subject_id` form a generic polymorphic pointer so we
    can answer "what happened to paper X" without separate tables per subject.
    `payload` carries event-specific data (filename, fields changed, etc.).
    `actor` is one of: "system" | "agent" | "user" - Phase 1b only emits
    "system" events; "user" / "agent" appear in Phase 4-5.
    """

    __tablename__ = "timeline_events"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
