"""Timeline HTTP API router (read-only).

Endpoints:
  GET /api/v1/workspaces/{wid}/timeline               list (filterable)
  GET /api/v1/workspaces/{wid}/timeline/{subject_type}/{subject_id}
                                                      list events for a subject
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.domains.timeline.schemas import TimelineListResponse, TimelineEventRead
from app.domains.timeline.service import TimelineService
from app.domains.workspace.service import WorkspaceNotFoundError, WorkspaceService

router = APIRouter(tags=["timeline"])


def _get_timeline_service(db: Session = Depends(get_db)) -> TimelineService:
    return TimelineService(db)


def _get_workspace_service(db: Session = Depends(get_db)) -> WorkspaceService:
    return WorkspaceService(db)


@router.get(
    "/workspaces/{workspace_id}/timeline",
    response_model=TimelineListResponse,
    response_model_exclude_unset=True,
)
def list_timeline(
    workspace_id: str,
    subject_type: str | None = Query(None),
    subject_id: str | None = Query(None),
    event_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: TimelineService = Depends(_get_timeline_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> TimelineListResponse:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "workspace_not_found", "message": str(e)},
        ) from e
    items, total = service.list(
        workspace_id=workspace_id,
        subject_type=subject_type,
        subject_id=subject_id,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return TimelineListResponse(
        items=[TimelineEventRead.model_validate(e) for e in items],
        total=total,
        limit=limit,
        offset=offset,
    )
