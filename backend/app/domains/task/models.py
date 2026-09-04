"""Task ORM 模型。

Task Runtime 状态机：
    queued -> running -> waiting_for_user | succeeded | failed
                                    |
                                    v
                          （由用户恢复）-> running
    queued | running -> cancel_requested -> cancelled

Phase 1b：仅包含表、状态机和 CRUD。实际的 Celery task wiring（parse_pdf、embed_chunks、
extract_knowledge）在 Phase 2-3 接入。
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Task(Base, UUIDPKMixin, TimestampMixin):
    """workspace 中的长时间运行任务。"""

    __tablename__ = "tasks"

    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True
    )
    task_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)

# 进度 0.0 - 1.0
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

# 自由格式的结构化信息：输入参数、当前步骤、部分结果。
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

# Celery 集成
    celery_task_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

# 软删除（仅管理员/清理使用，任务通常永久保留以供审计）
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
