"""Timeline HTTP API 路由（只读）。

接口：
  GET /api/v1/workspaces/{wid}/timeline               列表（可筛选）
  GET /api/v1/workspaces/{wid}/timeline/{subject_type}/{subject_id}
                                                      列出某个 subject 的事件

这里抛出的 domain exception 会由注册在 ``app.core.exception_handlers`` 中的集中处理器
转换为 HTTP 响应。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.domains.timeline.schemas import TimelineListResponse, TimelineEventRead
from app.domains.timeline.service import TimelineService
from app.domains.workspace.service import WorkspaceService

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
    user_id: str = Depends(get_current_user),
) -> TimelineListResponse:
    workspace_service.get(workspace_id, actor_id=user_id)
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
