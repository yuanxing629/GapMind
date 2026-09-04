"""Artifact HTTP API 路由。

Phase 1b：只读（list + get）。上传通过 Paper 路由完成，由其委托 ArtifactService.save_upload。
刻意不暴露直接上传端点——所有 Artifact 都应在 Paper（或后续 Task 结果）的上下文中创建。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_owned_workspace
from app.domains.artifact.schemas import ArtifactRead
from app.domains.artifact.service import ArtifactNotFoundError, ArtifactService
from app.domains.workspace.service import WorkspaceNotFoundError, WorkspaceService

router = APIRouter(tags=["artifact"], dependencies=[Depends(get_owned_workspace)])


def _get_artifact_service(db: Session = Depends(get_db)) -> ArtifactService:
    return ArtifactService(db)


def _get_workspace_service(db: Session = Depends(get_db)) -> WorkspaceService:
    return WorkspaceService(db)


def _not_found(exc: Exception) -> HTTPException:
    if isinstance(exc, ArtifactNotFoundError):
        code = "artifact_not_found"
    else:
        code = "workspace_not_found"
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": code, "message": str(exc)},
    )


@router.get(
    "/workspaces/{workspace_id}/artifacts",
    response_model=list[ArtifactRead],
    response_model_exclude_unset=True,
)
def list_artifacts(
    workspace_id: str,
    kind: str | None = Query(
        None,
        pattern=r"^(pdf|parsed_text|parsed_markdown|chunk_index|paper_image|report)$",
    ),
    paper_id: str | None = Query(None),
    artifact_service: ArtifactService = Depends(_get_artifact_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> list[ArtifactRead]:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    items = artifact_service.list_by_workspace(
        workspace_id,
        kind=kind,
        paper_id=paper_id,
    )
    return [ArtifactRead.model_validate(a) for a in items]


@router.get(
    "/workspaces/{workspace_id}/artifacts/{artifact_id}",
    response_model=ArtifactRead,
    response_model_exclude_unset=True,
)
def get_artifact(
    workspace_id: str,
    artifact_id: str,
    artifact_service: ArtifactService = Depends(_get_artifact_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> ArtifactRead:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    try:
        a = artifact_service.get(artifact_id)
    except ArtifactNotFoundError as e:
        raise _not_found(e) from e
    if a.workspace_id != workspace_id:
        raise _not_found(ArtifactNotFoundError(artifact_id))
    return ArtifactRead.model_validate(a)


@router.get("/workspaces/{workspace_id}/artifacts/{artifact_id}/download")
def download_artifact(
    workspace_id: str,
    artifact_id: str,
    artifact_service: ArtifactService = Depends(_get_artifact_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> FileResponse:
    """下载 artifact，包括 parsed_markdown 源文件。"""
    try:
        workspace_service.get(workspace_id)
        artifact = artifact_service.get(artifact_id)
    except (WorkspaceNotFoundError, ArtifactNotFoundError) as exc:
        raise _not_found(exc) from exc
    if artifact.workspace_id != workspace_id:
        raise _not_found(ArtifactNotFoundError(artifact_id))
    path = artifact_service.resolve_abs_path(artifact)
    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "artifact_file_missing", "message": "Artifact file is missing on disk"},
        )
    return FileResponse(
        path,
        media_type=artifact.mime_type or "application/octet-stream",
        filename=artifact.original_filename or path.name,
    )


@router.get("/workspaces/{workspace_id}/artifacts/{artifact_id}/view")
def view_artifact(
    workspace_id: str,
    artifact_id: str,
    artifact_service: ArtifactService = Depends(_get_artifact_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> FileResponse:
    """以内嵌方式提供 artifact，供应用内 PDF 阅读器使用。"""
    try:
        workspace_service.get(workspace_id)
        artifact = artifact_service.get(artifact_id)
    except (WorkspaceNotFoundError, ArtifactNotFoundError) as exc:
        raise _not_found(exc) from exc
    if artifact.workspace_id != workspace_id:
        raise _not_found(ArtifactNotFoundError(artifact_id))
    path = artifact_service.resolve_abs_path(artifact)
    if not path.exists() or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "artifact_file_missing", "message": "Artifact file is missing on disk"},
        )
    return FileResponse(
        path,
        media_type=artifact.mime_type or "application/octet-stream",
        filename=artifact.original_filename or path.name,
        content_disposition_type="inline",
    )
