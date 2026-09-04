"""Workspace service 层。

负责 CRUD、soft-delete 和 archive 的业务逻辑。API 层保持薄，并将调用转发到这里。Soft
delete（is_deleted=True）保留数据行用于审计和 Timeline 追溯；除非明确强制，MVP 中不做
hard-delete。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401  (type-only, kept for future)
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.workspace.access import has_workspace_access
from app.domains.workspace.models import Workspace
from app.domains.workspace.schemas import (
    WorkspaceCreate,
    WorkspaceUpdate,
)

logger = get_logger(__name__)

# standalone W7 agent 使用的系统 workspace（未选择用户 workspace）。
INDEPENDENT_WORKSPACE_NAME = "__independent__"


class WorkspaceNotFoundError(Exception):
    """workspace 查找失败时抛出的异常。"""

    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"Workspace not found: {workspace_id}")
        self.workspace_id = workspace_id


class WorkspaceService:
    """Workspace 的 CRUD 操作。"""

    def __init__(self, db: Session) -> None:
        self.db = db

# ------------------------------------------------------------------ 创建
    def create(self, payload: WorkspaceCreate, *, owner_id: str = "user") -> Workspace:
        ws = Workspace(
            id=str(uuid4()),
            name=payload.name,
            owner_id=owner_id,
            description=payload.description,
            topic=payload.topic,
            keywords=list(payload.keywords),
            goals=payload.goals,
            constraints=payload.constraints,
            active_questions=list(payload.active_questions),
            is_archived=False,
            is_demo=False,
            is_deleted=False,
        )
        self.db.add(ws)
        try:
            self.db.commit()
        except IntegrityError as e:
            self.db.rollback()
            raise RuntimeError(f"Failed to create workspace: {e}") from e
        self.db.refresh(ws)
        logger.info("workspace.created", workspace_id=ws.id, name=ws.name)
        return ws

# -------------------------------------------------------------------- 读取
    def get(self, workspace_id: str, *, actor_id: str | None = None) -> Workspace:
        self._validate_uuid(workspace_id)
        ws = self.db.get(Workspace, workspace_id)
        if ws is None or ws.is_deleted or (
            actor_id is not None and not has_workspace_access(self.db, workspace_id, actor_id)
        ):
            raise WorkspaceNotFoundError(workspace_id)
        return ws

    def list(
        self,
        *,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
        owner_id: str | None = None,
    ) -> tuple[list[Workspace], int]:
        """返回（items、total），并排除软删除行。"""
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        base = select(Workspace).where(Workspace.is_deleted.is_(False))
        if owner_id is not None:
            base = base.where(Workspace.owner_id == owner_id)
        if not include_archived:
            base = base.where(Workspace.is_archived.is_(False))

        items_q = base.order_by(Workspace.created_at.desc()).limit(limit).offset(offset)
        total_q = select(func.count()).select_from(base.subquery())

        items = list(self.db.execute(items_q).scalars().all())
        total = int(self.db.execute(total_q).scalar() or 0)
        return items, total

# ------------------------------------------------------------------ 更新
    def update(
        self, workspace_id: str, payload: WorkspaceUpdate, *, actor_id: str | None = None
    ) -> Workspace:
        ws = self.get(workspace_id, actor_id=actor_id)
        data = payload.model_dump(exclude_unset=True)

# 空字典表示没有字段需要更新，原样返回。
        if not data:
            return ws

        for field, value in data.items():
            if field in {"keywords", "active_questions"} and value is not None:
                value = list(value)
            setattr(ws, field, value)

        self.db.commit()
        self.db.refresh(ws)
        logger.info("workspace.updated", workspace_id=ws.id, fields=list(data.keys()))
        return ws

# ------------------------------------------------------------------ 删除
    def soft_delete(self, workspace_id: str, *, actor_id: str | None = None) -> None:
        ws = self.get(workspace_id, actor_id=actor_id)
        ws.is_deleted = True
        self.db.commit()
        logger.info("workspace.soft_deleted", workspace_id=ws.id)

    def archive(self, workspace_id: str, *, actor_id: str | None = None) -> Workspace:
        ws = self.get(workspace_id, actor_id=actor_id)
        ws.is_archived = True
        self.db.commit()
        self.db.refresh(ws)
        logger.info("workspace.archived", workspace_id=ws.id)
        return ws

    def get_or_create_independent(self, *, owner_id: str = "user") -> Workspace:
        """返回系统 independent workspace，不存在时创建。

        供 standalone W7 agents（analyze/write/respond）在用户尚未选择 workspace 时使用。
        独立空间没有语料库，因此 agent 只使用用户提供的输入（Discover/Plan 不可用）。
        """
        existing = self.db.scalar(
            select(Workspace).where(
                Workspace.name == INDEPENDENT_WORKSPACE_NAME,
                Workspace.owner_id == owner_id,
                Workspace.is_deleted.is_(False),
            ).limit(1)
        )
        if existing:
            return existing
        ws = Workspace(
            id=str(uuid4()),
            name=INDEPENDENT_WORKSPACE_NAME,
            owner_id=owner_id,
            description="独立模式空间：供未选择课题空间的 W7 分析/写作/审稿使用",
            is_archived=False,
            is_demo=False,
            is_deleted=False,
        )
        self.db.add(ws)
        self.db.commit()
        self.db.refresh(ws)
        return ws

    def unarchive(self, workspace_id: str, *, actor_id: str | None = None) -> Workspace:
        ws = self.get(workspace_id, actor_id=actor_id)
        ws.is_archived = False
        self.db.commit()
        self.db.refresh(ws)
        logger.info("workspace.unarchived", workspace_id=ws.id)
        return ws

    @staticmethod
    def _validate_uuid(workspace_id: str) -> None:
        try:
            UUID(str(workspace_id))
        except (ValueError, TypeError) as e:
            raise WorkspaceNotFoundError(workspace_id) from e
