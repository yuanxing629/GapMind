"""Task HTTP API 路由。

接口：
  GET    /api/v1/workspaces/{wid}/tasks          列表（可按状态筛选）
  GET    /api/v1/tasks/{tid}                     获取单个任务（通过任务行限定 workspace）
  POST   /api/v1/tasks/{tid}/cancel              请求取消
  POST   /api/v1/tasks/{tid}/resume              从 waiting_for_user 恢复
  POST   /api/v1/tasks/{tid}/retry               失败任务重新入队

Phase 1b 不通过 HTTP 暴露 task *creation*；用户上传 PDF 或请求 opportunity discovery 时，
由系统（Phase 2 worker）创建 task。在这里暴露创建接口会使用户能够随意生成任意 task type。

这里抛出的 domain exception 会由注册在 ``app.core.exception_handlers`` 中的集中处理器
转换为 HTTP 响应。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.domains.task.schemas import TaskListResponse, TaskRead
from app.domains.task.service import TaskService
from app.domains.workspace.service import WorkspaceService

router = APIRouter(tags=["task"])


def _get_task_service(db: Session = Depends(get_db)) -> TaskService:
    return TaskService(db)


def _get_workspace_service(db: Session = Depends(get_db)) -> WorkspaceService:
    return WorkspaceService(db)


class ResumeBody(BaseModel):
    decision: dict | None = None


def _assert_task_owner(
    task,
    *,
    workspace_service: WorkspaceService,
    user_id: str,
) -> None:
    """对于没有用户可见 Workspace 所有者的任务行，采用 fail-closed。"""
    if not task.workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "task_not_found",
                "message": "Task is not attached to an accessible workspace",
            },
        )
    workspace_service.get(task.workspace_id, actor_id=user_id)


@router.get(
    "/workspaces/{workspace_id}/tasks",
    response_model=TaskListResponse,
    response_model_exclude_unset=True,
)
def list_tasks(
    workspace_id: str,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: TaskService = Depends(_get_task_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
    user_id: str = Depends(get_current_user),
) -> TaskListResponse:
    workspace_service.get(workspace_id, actor_id=user_id)
    items, total = service.list(
        workspace_id=workspace_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return TaskListResponse(
        items=[TaskRead.model_validate(t) for t in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/tasks/{task_id}",
    response_model=TaskRead,
    response_model_exclude_unset=True,
)
def get_task(
    task_id: str,
    service: TaskService = Depends(_get_task_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
    user_id: str = Depends(get_current_user),
) -> TaskRead:
    task = service.get(task_id)
    _assert_task_owner(task, workspace_service=workspace_service, user_id=user_id)
    return TaskRead.model_validate(task)


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=TaskRead,
    response_model_exclude_unset=True,
)
def cancel_task(
    task_id: str,
    service: TaskService = Depends(_get_task_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
    user_id: str = Depends(get_current_user),
) -> TaskRead:
    task = service.get(task_id)
    _assert_task_owner(task, workspace_service=workspace_service, user_id=user_id)
    return TaskRead.model_validate(service.request_cancel(task_id))


@router.post(
    "/tasks/{task_id}/resume",
    response_model=TaskRead,
    response_model_exclude_unset=True,
)
def resume_task(
    task_id: str,
    body: ResumeBody,
    service: TaskService = Depends(_get_task_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
    user_id: str = Depends(get_current_user),
) -> TaskRead:
    task = service.get(task_id)
    _assert_task_owner(task, workspace_service=workspace_service, user_id=user_id)
    return TaskRead.model_validate(service.resume_from_user(task_id, decision=body.decision))


@router.post(
    "/tasks/{task_id}/retry",
    response_model=TaskRead,
    response_model_exclude_unset=True,
)
def retry_task(
    task_id: str,
    service: TaskService = Depends(_get_task_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
    user_id: str = Depends(get_current_user),
) -> TaskRead:
    task = service.get(task_id)
    _assert_task_owner(task, workspace_service=workspace_service, user_id=user_id)
    return TaskRead.model_validate(service.retry(task_id))
