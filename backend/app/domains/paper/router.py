"""Paper HTTP API router.

Endpoints:
  POST   /api/v1/workspaces/{wid}/papers/upload        multipart upload + create
  POST   /api/v1/workspaces/{wid}/papers               JSON metadata-only create
  GET    /api/v1/workspaces/{wid}/papers               list (paginated)
  GET    /api/v1/workspaces/{wid}/papers/{pid}         get one
  PATCH  /api/v1/workspaces/{wid}/papers/{pid}         update
  DELETE /api/v1/workspaces/{wid}/papers/{pid}         soft delete
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.logging import get_logger
from app.domains.artifact.service import ArtifactNotFoundError
from app.domains.paper.schemas import (
    PaperCreate,
    PaperListResponse,
    PaperRead,
    PaperUpdate,
)
from app.domains.paper.service import (
    PaperAlreadyHasPdfError,
    PaperNotFoundError,
    PaperService,
)
from app.domains.workspace.service import WorkspaceNotFoundError, WorkspaceService

logger = get_logger(__name__)
router = APIRouter(tags=["paper"])

MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB


def _get_paper_service(db: Session = Depends(get_db)) -> PaperService:
    return PaperService(db)


def _get_workspace_service(db: Session = Depends(get_db)) -> WorkspaceService:
    return WorkspaceService(db)


def _not_found(exc: Exception) -> HTTPException:
    if isinstance(exc, PaperNotFoundError):
        code = "paper_not_found"
    elif isinstance(exc, ArtifactNotFoundError):
        code = "artifact_not_found"
    else:
        code = "workspace_not_found"
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": code, "message": str(exc)},
    )


def _conflict(exc: PaperAlreadyHasPdfError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "paper_already_has_pdf", "message": str(exc)},
    )


@router.post(
    "/workspaces/{workspace_id}/papers/upload",
    response_model=PaperRead,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
)
async def upload_paper(
    workspace_id: str,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    authors: str | None = Form(None),
    year: int | None = Form(None),
    abstract: str | None = Form(None),
    doi: str | None = Form(None),
    arxiv_id: str | None = Form(None),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_file", "message": "A .pdf file is required"},
        )

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_file", "message": "Uploaded file is empty"},
        )
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "file_too_large",
                "message": f"PDF exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB",
            },
        )

    # When the user doesn't supply a title, pass None - the service will
    # fill it from PDF metadata, or fall back to the filename stem.
    try:
        payload = PaperCreate(
            title=title,  # may be None; service handles fallback
            authors=_split_authors(authors),
            year=year,
            abstract=abstract,
            doi=doi,
            arxiv_id=arxiv_id,
        )
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "validation_error", "message": e.errors()},
        ) from e

    paper = service.create_from_upload(
        workspace_id=workspace_id,
        payload=payload,
        filename=file.filename,
        content=content,
        mime_type=file.content_type or "application/pdf",
    )
    return PaperRead.model_validate(paper)


@router.post(
    "/workspaces/{workspace_id}/papers/{paper_id}/upload-pdf",
    response_model=PaperRead,
    response_model_exclude_unset=True,
)
async def attach_pdf_to_paper(
    workspace_id: str,
    paper_id: str,
    file: UploadFile = File(...),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    """Attach a PDF to an existing metadata-only paper.

    Use case: paper was created via `POST /papers` (metadata only), and the
    user later obtains the PDF. Any empty metadata fields on the paper row
    are best-effort filled from the PDF's embedded metadata.
    """
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_file", "message": "A .pdf file is required"},
        )
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_file", "message": "Uploaded file is empty"},
        )
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "error": "file_too_large",
                "message": f"PDF exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB",
            },
        )

    try:
        paper = service.attach_pdf_to_existing(
            workspace_id=workspace_id,
            paper_id=paper_id,
            filename=file.filename,
            content=content,
            mime_type=file.content_type or "application/pdf",
        )
    except PaperNotFoundError as e:
        raise _not_found(e) from e
    except PaperAlreadyHasPdfError as e:
        raise _conflict(e) from e
    return PaperRead.model_validate(paper)


@router.post(
    "/workspaces/{workspace_id}/papers",
    response_model=PaperRead,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
)
def create_paper(
    workspace_id: str,
    payload: PaperCreate,
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    # For JSON metadata-only creation, title is required (can't be empty
    # since there's no PDF to fall back on for a title).
    if not payload.title or not payload.title.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "validation_error", "message": "title is required"},
        )
    paper = service.create_from_metadata(workspace_id=workspace_id, payload=payload)
    return PaperRead.model_validate(paper)


@router.get(
    "/workspaces/{workspace_id}/papers",
    response_model=PaperListResponse,
    response_model_exclude_unset=True,
)
def list_papers(
    workspace_id: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperListResponse:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    items, total = service.list(workspace_id=workspace_id, limit=limit, offset=offset)
    return PaperListResponse(
        items=[PaperRead.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/workspaces/{workspace_id}/papers/{paper_id}",
    response_model=PaperRead,
    response_model_exclude_unset=True,
)
def get_paper(
    workspace_id: str,
    paper_id: str,
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    try:
        paper = service.get(paper_id)
    except PaperNotFoundError as e:
        raise _not_found(e) from e
    if paper.workspace_id != workspace_id:
        raise _not_found(PaperNotFoundError(paper_id))
    return PaperRead.model_validate(paper)


@router.patch(
    "/workspaces/{workspace_id}/papers/{paper_id}",
    response_model=PaperRead,
    response_model_exclude_unset=True,
)
def update_paper(
    workspace_id: str,
    paper_id: str,
    payload: PaperUpdate,
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    try:
        paper = service.get(paper_id)
    except PaperNotFoundError as e:
        raise _not_found(e) from e
    if paper.workspace_id != workspace_id:
        raise _not_found(PaperNotFoundError(paper_id))
    paper = service.update(paper_id, payload)
    return PaperRead.model_validate(paper)


@router.delete(
    "/workspaces/{workspace_id}/papers/{paper_id}",
    status_code=status.HTTP_200_OK,
)
def delete_paper(
    workspace_id: str,
    paper_id: str,
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> dict[str, str | bool]:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    try:
        paper = service.get(paper_id)
    except PaperNotFoundError as e:
        raise _not_found(e) from e
    if paper.workspace_id != workspace_id:
        raise _not_found(PaperNotFoundError(paper_id))
    service.soft_delete(paper_id)
    return {"id": paper_id, "deleted": True}


# ----------------------------------------------------------------- helpers
def _stem(filename: str) -> str:
    """Return the filename without extension, used as a default paper title."""
    import os

    return os.path.splitext(filename)[0] or filename


def _split_authors(raw: str | None) -> list[str]:
    """Split a comma-or-newline-separated author string into a list."""
    if not raw:
        return []
    return [a.strip() for a in raw.replace("\n", ",").split(",") if a.strip()]
