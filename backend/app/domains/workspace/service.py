"""Workspace service layer.

Business logic for CRUD + soft-delete + archive. The API layer is thin and
delegates here. Soft delete (is_deleted=True) keeps the row for audit and
Timeline traceability - we never hard-delete in MVP unless explicitly forced.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401  (type-only, kept for future)
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.workspace.models import Workspace
from app.domains.workspace.schemas import (
    WorkspaceCreate,
    WorkspaceUpdate,
)

logger = get_logger(__name__)


class WorkspaceNotFoundError(Exception):
    """Raised when a workspace lookup fails."""

    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"Workspace not found: {workspace_id}")
        self.workspace_id = workspace_id


class WorkspaceService:
    """CRUD operations for Workspace."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------ create
    def create(self, payload: WorkspaceCreate) -> Workspace:
        ws = Workspace(
            id=str(uuid4()),
            name=payload.name,
            description=payload.description,
            topic=payload.topic,
            keywords=list(payload.keywords),
            goals=payload.goals,
            constraints=payload.constraints,
            active_questions=list(payload.active_questions),
            is_archived=False,
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

    # -------------------------------------------------------------------- read
    def get(self, workspace_id: str) -> Workspace:
        self._validate_uuid(workspace_id)
        ws = self.db.get(Workspace, workspace_id)
        if ws is None or ws.is_deleted:
            raise WorkspaceNotFoundError(workspace_id)
        return ws

    def list(
        self,
        *,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Workspace], int]:
        """Return (items, total) with soft-deleted rows excluded."""
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        base = select(Workspace).where(Workspace.is_deleted.is_(False))
        if not include_archived:
            base = base.where(Workspace.is_archived.is_(False))

        items_q = base.order_by(Workspace.created_at.desc()).limit(limit).offset(offset)
        total_q = select(func.count()).select_from(base.subquery())

        items = list(self.db.execute(items_q).scalars().all())
        total = int(self.db.execute(total_q).scalar() or 0)
        return items, total

    # ------------------------------------------------------------------ update
    def update(self, workspace_id: str, payload: WorkspaceUpdate) -> Workspace:
        ws = self.get(workspace_id)
        data = payload.model_dump(exclude_unset=True)

        # Empty dict means no fields to update - return as-is.
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

    # ------------------------------------------------------------------ delete
    def soft_delete(self, workspace_id: str) -> None:
        ws = self.get(workspace_id)
        ws.is_deleted = True
        self.db.commit()
        logger.info("workspace.soft_deleted", workspace_id=ws.id)

    def archive(self, workspace_id: str) -> Workspace:
        ws = self.get(workspace_id)
        ws.is_archived = True
        self.db.commit()
        self.db.refresh(ws)
        logger.info("workspace.archived", workspace_id=ws.id)
        return ws

    def unarchive(self, workspace_id: str) -> Workspace:
        ws = self.get(workspace_id)
        ws.is_archived = False
        self.db.commit()
        self.db.refresh(ws)
        logger.info("workspace.unarchived", workspace_id=ws.id)
        return ws

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _validate_uuid(workspace_id: str) -> None:
        try:
            UUID(str(workspace_id))
        except (ValueError, TypeError) as e:
            raise WorkspaceNotFoundError(workspace_id) from e
