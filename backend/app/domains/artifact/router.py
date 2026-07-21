"""Artifact HTTP API router.

Phase 1b: read-only (list + get). Upload happens through the Paper router
which delegates to ArtifactService.save_upload. A direct upload endpoint
is intentionally not exposed - all artifacts should be created in the
context of a Paper (or later, a Task result).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.domains.artifact.schemas import ArtifactRead
from app.domains.artifact.service import ArtifactNotFoundError, ArtifactService
from app.domains.workspace.service import WorkspaceNotFoundError, WorkspaceService

router = APIRouter(tags=["artifact"])


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
    kind: str | None = Query(None, pattern=r"^(pdf|parsed_text|chunk_index|report)$"),
    artifact_service: ArtifactService = Depends(_get_artifact_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> list[ArtifactRead]:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    items = artifact_service.list_by_workspace(workspace_id, kind=kind)
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
