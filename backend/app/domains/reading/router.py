"""论文阅读库的 HTTP API。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.domains.reading.schemas import (
    PaperAnnotationCreate,
    PaperAnnotationRead,
    PaperAnnotationUpdate,
    ReadingPaperListResponse,
    ReadingPaperRead,
    ReadingProgressUpdate,
)
from app.domains.reading.service import (
    ReadingAnnotationNotFoundError,
    ReadingPaperNotFoundError,
    ReadingService,
)

router = APIRouter(prefix="/reading", tags=["reading"])


def _service(db: Session = Depends(get_db)) -> ReadingService:
    return ReadingService(db)


def _paper_read(row: tuple) -> ReadingPaperRead:
    item, paper, workspace = row
    return ReadingPaperRead(
        reading_item_id=item.id,
        paper_id=paper.id,
        workspace_id=paper.workspace_id,
        workspace_name=workspace.name if workspace else None,
        title=paper.title,
        authors=paper.authors,
        year=paper.year,
        abstract=paper.abstract,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        source=paper.source,
        external_paper_id=paper.external_paper_id,
        primary_artifact_id=paper.primary_artifact_id,
        parse_status=paper.parse_status,
        page_count=paper.page_count,
        parsed_text_chars=paper.parsed_text_chars,
        quality_flags=paper.quality_flags or [],
        parse_error=paper.parse_error,
        parsed_markdown_artifact_id=paper.parsed_markdown_artifact_id,
        chunk_count=paper.chunk_count,
        reading_status=item.status,
        last_read_page=item.last_read_page,
        last_read_at=item.last_read_at,
        added_at=item.created_at,
        updated_at=item.updated_at,
    )


def _not_found(exc: Exception) -> dict[str, str]:
    if isinstance(exc, ReadingAnnotationNotFoundError):
        return {"error": "annotation_not_found", "message": str(exc)}
    return {"error": "reading_paper_not_found", "message": str(exc)}


@router.get("/papers", response_model=ReadingPaperListResponse)
def list_reading_papers(
    workspace_id: str | None = Query(None),
    reading_status: Literal["unread", "reading", "completed"] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: ReadingService = Depends(_service),
    user_id: str = Depends(get_current_user),
) -> ReadingPaperListResponse:
    rows, total = service.list_items(
        workspace_id=workspace_id,
        status=reading_status,
        limit=limit,
        offset=offset,
        actor_id=user_id,
    )
    return ReadingPaperListResponse(
        items=[_paper_read(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/papers/{paper_id}", response_model=ReadingPaperRead)
def get_reading_paper(
    paper_id: str,
    service: ReadingService = Depends(_service),
    user_id: str = Depends(get_current_user),
) -> ReadingPaperRead:
    try:
        return _paper_read(service.get_item(paper_id, actor_id=user_id))
    except ReadingPaperNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=_not_found(exc)) from exc


@router.post(
    "/papers/{paper_id}",
    response_model=ReadingPaperRead,
    status_code=status.HTTP_201_CREATED,
)
def add_reading_paper(
    paper_id: str,
    service: ReadingService = Depends(_service),
    user_id: str = Depends(get_current_user),
) -> ReadingPaperRead:
    try:
        return _paper_read(service.add_item(paper_id, actor_id=user_id))
    except ReadingPaperNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=_not_found(exc)) from exc


@router.delete("/papers/{paper_id}")
def remove_reading_paper(
    paper_id: str,
    service: ReadingService = Depends(_service),
    user_id: str = Depends(get_current_user),
) -> dict[str, str | bool]:
    try:
        service.remove_item(paper_id, actor_id=user_id)
    except ReadingPaperNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=_not_found(exc)) from exc
    return {"paper_id": paper_id, "deleted": True}


@router.patch("/papers/{paper_id}/progress", response_model=ReadingPaperRead)
def update_reading_progress(
    paper_id: str,
    payload: ReadingProgressUpdate,
    service: ReadingService = Depends(_service),
    user_id: str = Depends(get_current_user),
) -> ReadingPaperRead:
    try:
        return _paper_read(service.update_progress(paper_id, payload, actor_id=user_id))
    except ReadingPaperNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=_not_found(exc)) from exc


@router.get("/papers/{paper_id}/annotations", response_model=list[PaperAnnotationRead])
def list_annotations(
    paper_id: str,
    service: ReadingService = Depends(_service),
    user_id: str = Depends(get_current_user),
) -> list[PaperAnnotationRead]:
    try:
        return [
            PaperAnnotationRead.model_validate(a)
            for a in service.list_annotations(paper_id, actor_id=user_id)
        ]
    except ReadingPaperNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=_not_found(exc)) from exc


@router.post(
    "/papers/{paper_id}/annotations",
    response_model=PaperAnnotationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_annotation(
    paper_id: str,
    payload: PaperAnnotationCreate,
    service: ReadingService = Depends(_service),
    user_id: str = Depends(get_current_user),
) -> PaperAnnotationRead:
    try:
        return PaperAnnotationRead.model_validate(
            service.create_annotation(paper_id, payload, actor_id=user_id)
        )
    except ReadingPaperNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=_not_found(exc)) from exc


@router.patch("/annotations/{annotation_id}", response_model=PaperAnnotationRead)
def update_annotation(
    annotation_id: str,
    payload: PaperAnnotationUpdate,
    service: ReadingService = Depends(_service),
    user_id: str = Depends(get_current_user),
) -> PaperAnnotationRead:
    try:
        return PaperAnnotationRead.model_validate(
            service.update_annotation(annotation_id, payload, actor_id=user_id)
        )
    except ReadingAnnotationNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=_not_found(exc)) from exc


@router.delete("/annotations/{annotation_id}")
def remove_annotation(
    annotation_id: str,
    service: ReadingService = Depends(_service),
    user_id: str = Depends(get_current_user),
) -> dict[str, str | bool]:
    try:
        service.remove_annotation(annotation_id, actor_id=user_id)
    except ReadingAnnotationNotFoundError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail=_not_found(exc)) from exc
    return {"annotation_id": annotation_id, "deleted": True}
