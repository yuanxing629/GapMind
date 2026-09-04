"""Workspace ORM 模型。

Workspace 是核心作用域对象：每个 Paper、KnowledgeItem、Opportunity、Task 和
TimelineEvent 都恰好属于一个 Workspace。
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Workspace(Base, UUIDPKMixin, TimestampMixin):
    """研究 workspace：一条研究线程的作用域。

    内嵌 Research Profile（topic、keywords、goals、constraints、active_questions）以保持
    Phase 1 简单。如果 profile 变得复杂，后续可以拆分为独立的 `research_profiles` 表。
    """

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_id: Mapped[str] = mapped_column(
        String(128), default="user", server_default="user", nullable=False, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

# Research Profile（MVP 内嵌）
    topic: Mapped[str | None] = mapped_column(Text, nullable=True)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    constraints: Mapped[str | None] = mapped_column(Text, nullable=True)
    active_questions: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

# 生命周期
    is_archived: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    is_demo: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False, index=True
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
