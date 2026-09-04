"""Timeline ORM 模型。

每当发生有意义的研究活动时，系统都会自动记录 TimelineEvent，例如创建 workspace、上传
paper、切换 task 状态等。用户不能直接写入 Timeline 条目。
"""

from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class TimelineEvent(Base, UUIDPKMixin, TimestampMixin):
    """一条自动记录的研究活动事件。

    `subject_type` + `subject_id` 构成通用的多态指针，因此无需为每种 subject 单独建表，
    也能回答“paper X 发生了什么”。`payload` 携带事件专属数据（文件名、变更字段等）。
    `actor` 保存简短的 system/agent 标签，或认证用户的平台身份 token。
    """

    __tablename__ = "timeline_events"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), default="system", nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
