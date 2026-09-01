"""Workspace HTTP API router.

Endpoints:
  POST   /api/v1/workspaces                       create
  GET    /api/v1/workspaces                       list (paginated, archived excluded by default)
  GET    /api/v1/workspaces/{id}                  get one
  PATCH  /api/v1/workspaces/{id}                  update
  POST   /api/v1/workspaces/{id}/archive          archive
  POST   /api/v1/workspaces/{id}/unarchive        unarchive
  DELETE /api/v1/workspaces/{id}                  soft delete (returns 200 + {"deleted": true})

Domain exceptions raised here are translated into HTTP responses by the
central handler registered in ``app.core.exception_handlers``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.domains.workspace.readiness import WorkspaceReadinessService
from app.domains.workspace.schemas import (
    WorkspaceCreate,
    WorkspaceListResponse,
    WorkspaceRead,
    WorkspaceReadiness,
    WorkspaceUpdate,
)
from app.domains.workspace.service import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["workspace"])


def _get_service(db: Session = Depends(get_db)) -> WorkspaceService:
    return WorkspaceService(db)


@router.post(
    "",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
)
def create_workspace(
    payload: WorkspaceCreate,
    service: WorkspaceService = Depends(_get_service),
    user_id: str = Depends(get_current_user),
) -> WorkspaceRead:
    ws = service.create(payload, owner_id=user_id)
    return WorkspaceRead.model_validate(ws)


@router.get(
    "",
    response_model=WorkspaceListResponse,
    response_model_exclude_unset=True,
)
def list_workspaces(
    include_archived: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: WorkspaceService = Depends(_get_service),
    user_id: str = Depends(get_current_user),
) -> WorkspaceListResponse:
    items, total = service.list(
        include_archived=include_archived, limit=limit, offset=offset, owner_id=user_id
    )
    return WorkspaceListResponse(
        items=[WorkspaceRead.model_validate(w) for w in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/independent",
    response_model=WorkspaceRead,
    response_model_exclude_unset=True,
)
def get_independent_workspace(
    service: WorkspaceService = Depends(_get_service),
    user_id: str = Depends(get_current_user),
) -> WorkspaceRead:
    """System independent workspace for standalone W7 agents (no workspace selected)."""
    return WorkspaceRead.model_validate(service.get_or_create_independent(owner_id=user_id))


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceRead,
    response_model_exclude_unset=True,
)
def get_workspace(
    workspace_id: str,
    service: WorkspaceService = Depends(_get_service),
    user_id: str = Depends(get_current_user),
) -> WorkspaceRead:
    return WorkspaceRead.model_validate(service.get(workspace_id, actor_id=user_id))


@router.get(
    "/{workspace_id}/readiness",
    response_model=WorkspaceReadiness,
    response_model_exclude_unset=True,
)
def get_workspace_readiness(
    workspace_id: str,
    workspace_service: WorkspaceService = Depends(_get_service),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> WorkspaceReadiness:
    """Research readiness for one workspace (W0): five dimensions + next action.

    Single source of truth for the overview progress bar and "why not /
    where to go" explanations. Raises 404 if the workspace is missing.
    """
    workspace = workspace_service.get(workspace_id, actor_id=user_id)
    return WorkspaceReadiness.model_validate(
        WorkspaceReadinessService(db).get_readiness(workspace)
    )


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceRead,
    response_model_exclude_unset=True,
)
def update_workspace(
    workspace_id: str,
    payload: WorkspaceUpdate,
    service: WorkspaceService = Depends(_get_service),
    user_id: str = Depends(get_current_user),
) -> WorkspaceRead:
    return WorkspaceRead.model_validate(service.update(workspace_id, payload, actor_id=user_id))


@router.post(
    "/{workspace_id}/archive",
    response_model=WorkspaceRead,
    response_model_exclude_unset=True,
)
def archive_workspace(
    workspace_id: str,
    service: WorkspaceService = Depends(_get_service),
    user_id: str = Depends(get_current_user),
) -> WorkspaceRead:
    return WorkspaceRead.model_validate(service.archive(workspace_id, actor_id=user_id))


@router.post(
    "/{workspace_id}/unarchive",
    response_model=WorkspaceRead,
    response_model_exclude_unset=True,
)
def unarchive_workspace(
    workspace_id: str,
    service: WorkspaceService = Depends(_get_service),
    user_id: str = Depends(get_current_user),
) -> WorkspaceRead:
    return WorkspaceRead.model_validate(service.unarchive(workspace_id, actor_id=user_id))


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_200_OK,
)
def delete_workspace(
    workspace_id: str,
    service: WorkspaceService = Depends(_get_service),
    user_id: str = Depends(get_current_user),
) -> dict[str, str | bool]:
    service.soft_delete(workspace_id, actor_id=user_id)
    return {"id": workspace_id, "deleted": True}
