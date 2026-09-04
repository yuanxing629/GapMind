"""Workspace HTTP API 路由。

接口：
  POST   /api/v1/workspaces                       创建
  GET    /api/v1/workspaces                       列表（分页，默认排除已归档）
  GET    /api/v1/workspaces/{id}                  获取单个 workspace
  PATCH  /api/v1/workspaces/{id}                  更新
  POST   /api/v1/workspaces/{id}/archive          归档
  POST   /api/v1/workspaces/{id}/unarchive        取消归档
  DELETE /api/v1/workspaces/{id}                  软删除（返回 200 + {"deleted": true}）

这里抛出的 domain exception 会由注册在 ``app.core.exception_handlers`` 中的集中处理器
转换为 HTTP 响应。
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
    """standalone W7 agent 使用的系统 independent workspace（未选择 workspace）。"""
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
    """单个 workspace 的研究就绪度（W0）：五个维度和下一步操作。

    概览进度条和“为什么未就绪 / 下一步去哪里”说明的单一事实来源。workspace 不存在时
    返回 404。
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
