"""Workspace ORM models.

The Workspace is the core scope object: every Paper, KnowledgeItem,
Opportunity, Task, and TimelineEvent belongs to exactly one Workspace.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Workspace(Base, UUIDPKMixin, TimestampMixin):
    """A research workspace - the scope for one research thread.

    Carries the Research Profile inline (topic, keywords, goals, constraints,
    active_questions) to keep Phase 1 simple. If the profile grows complex
    we can split it into a separate `research_profiles` table later.
    """

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Research Profile (inline for MVP)
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    constraints: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_questions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # Lifecycle
    is_archived: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
