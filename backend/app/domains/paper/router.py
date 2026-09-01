"""Paper HTTP API router.

Endpoints:
  POST   /api/v1/workspaces/{wid}/papers/upload        multipart upload + create
  POST   /api/v1/workspaces/{wid}/papers               JSON metadata-only create
  GET    /api/v1/workspaces/{wid}/papers               list (paginated)
  GET    /api/v1/workspaces/{wid}/papers/{pid}         get one
  PATCH  /api/v1/workspaces/{wid}/papers/{pid}         update
  DELETE /api/v1/workspaces/{wid}/papers/{pid}         soft delete

Domain exceptions raised here (PaperNotFoundError, WorkspaceNotFoundError,
PaperAlreadyHasPdfError, SemanticScholarError) are translated into HTTP
responses by the central handler registered in
``app.core.exception_handlers``. Business-rule violations (empty file,
wrong extension, paper not parsed, …) stay as inline ``HTTPException``
because they're not tied to any exception class.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db, get_owned_workspace
from app.core.logging import get_logger
from app.domains.paper.schemas import (
    PaperCreate,
    PaperListResponse,
    PaperRead,
    PaperUpdate,
    SemanticScholarFavoriteCreate,
    SemanticScholarFavoriteRead,
    SemanticScholarImportRequest,
    SemanticScholarSearchHistoryRead,
    SemanticScholarSearchResponse,
)
from app.domains.paper.service import PaperService
from app.domains.paper.search_service import PaperSearchService
from app.domains.workspace.service import WorkspaceService
from app.gateway.semantic_scholar import SemanticScholarClient, SemanticScholarError

logger = get_logger(__name__)
router = APIRouter(tags=["paper"])

MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB
UPLOAD_READ_CHUNK_BYTES = 1024 * 1024
S2_SEARCH_FIELDS = (
    "paperId,corpusId,externalIds,title,abstract,year,publicationDate,authors,"
    "venue,url,citationCount,referenceCount,influentialCitationCount,isOpenAccess,"
    "openAccessPdf,fieldsOfStudy,s2FieldsOfStudy,publicationTypes"
)
S2_IMPORT_FIELDS = "paperId,externalIds,title,abstract,year,authors,openAccessPdf"
ARXIV_ID_PATTERN = re.compile(
    r"^(?:\d{4}\.\d{4,5}(?:v\d+)?|[A-Za-z][A-Za-z-]*(?:\.[A-Za-z]{2})?/\d{7}(?:v\d+)?)$"
)


def _get_paper_service(db: Session = Depends(get_db)) -> PaperService:
    return PaperService(db)


def _get_workspace_service(db: Session = Depends(get_db)) -> WorkspaceService:
    return WorkspaceService(db)


def _get_semantic_scholar_client() -> SemanticScholarClient:
    return SemanticScholarClient()


async def _read_pdf_upload(file: UploadFile) -> bytes:
    """Read an uploaded PDF with a bounded memory budget and magic check.

    The declared MIME type is supplied by the client and is therefore only
    advisory.  The extension is checked by the endpoint, while this helper
    checks the PDF signature and reads in chunks so a forged ``Content-Length``
    cannot make the process hold an unbounded upload in memory.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_PDF_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "error": "file_too_large",
                    "message": f"PDF exceeds {MAX_PDF_BYTES // (1024 * 1024)} MB",
                },
            )
        chunks.append(chunk)

    content = b"".join(chunks)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "empty_file", "message": "Uploaded file is empty"},
        )
    if not content.startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_pdf",
                "message": "Uploaded file does not have a valid PDF signature",
            },
        )
    return content


def _arxiv_pdf_url(external_ids: dict[str, object]) -> str | None:
    """Build a safe arXiv PDF URL from Semantic Scholar external IDs."""
    arxiv_id = _external_id_as_string(external_ids, "ArXiv", "ARXIV")
    if not arxiv_id:
        return None
    arxiv_id = arxiv_id.strip()
    if arxiv_id.lower().startswith("arxiv:"):
        arxiv_id = arxiv_id.split(":", 1)[1].strip()
    if arxiv_id.lower().endswith(".pdf"):
        arxiv_id = arxiv_id[:-4]
    if not ARXIV_ID_PATTERN.fullmatch(arxiv_id):
        return None
    return f"https://arxiv.org/pdf/{quote(arxiv_id, safe='/')}"


@router.get(
    "/papers/search",
    response_model=SemanticScholarSearchResponse,
    response_model_exclude_unset=True,
)
def search_external_papers(
    query: str = Query(..., min_length=2, max_length=200),
    year_from: int | None = Query(None, ge=1900, le=2100),
    year_to: int | None = Query(None, ge=1900, le=2100),
    min_citation_count: int | None = Query(None, ge=0, le=10_000_000),
    open_access: bool = Query(False),
    fields_of_study: str | None = Query(None, max_length=500),
    publication_types: str | None = Query(None, max_length=500),
    venue: str | None = Query(None, max_length=500),
    sort: Literal[
        "relevance",
        "publicationDate:asc",
        "publicationDate:desc",
        "citationCount:asc",
        "citationCount:desc",
    ] = Query("relevance"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    token: str | None = Query(None, max_length=500),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
    client: SemanticScholarClient = Depends(_get_semantic_scholar_client),
) -> SemanticScholarSearchResponse:
    """Search Semantic Scholar without exposing the upstream API key."""
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_year_range", "message": "year_from must be <= year_to"},
        )

    year_filter: str | None = None
    if year_from is not None or year_to is not None:
        year_filter = f"{year_from or ''}-{year_to or ''}"

    raw = client.search(
        query=query.strip(),
        fields=S2_SEARCH_FIELDS,
        sort=sort,
        limit=limit,
        offset=offset,
        token=token,
        year=year_filter,
        minCitationCount=min_citation_count,
        openAccessPdf="" if open_access else None,
        fieldsOfStudy=fields_of_study,
        publicationTypes=publication_types,
        venue=venue,
    )

    try:
        PaperSearchService(db).record_history(
            query=query.strip(),
            filters={
                "year_from": year_from,
                "year_to": year_to,
                "min_citation_count": min_citation_count,
                "open_access": open_access,
                "fields_of_study": fields_of_study.split(",") if fields_of_study else [],
                "publication_types": publication_types.split(",") if publication_types else [],
                "venue": venue,
            },
            sort=sort,
            result_count=int(raw.get("total") or len(raw.get("data") or [])),
            actor_id=user_id,
        )
    except Exception as exc:
        db.rollback()
        logger.warning("semantic_scholar.history_record_failed", error=str(exc))

    return SemanticScholarSearchResponse.model_validate(raw)


@router.get("/papers/search/history", response_model=list[SemanticScholarSearchHistoryRead])
def list_search_history(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SemanticScholarSearchHistoryRead]:
    rows = PaperSearchService(db).list_history(
        limit=limit, offset=offset, actor_id=user_id
    )
    return [SemanticScholarSearchHistoryRead.model_validate(row) for row in rows]


@router.delete("/papers/search/history/{history_id}")
def delete_search_history(
    history_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if not PaperSearchService(db).delete_history(history_id, actor_id=user_id):
        raise HTTPException(status_code=404, detail={"error": "search_history_not_found"})
    return {"deleted": True}


@router.get("/papers/favorites", response_model=list[SemanticScholarFavoriteRead])
def list_search_favorites(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SemanticScholarFavoriteRead]:
    rows = PaperSearchService(db).list_favorites(
        limit=limit, offset=offset, actor_id=user_id
    )
    return [SemanticScholarFavoriteRead.model_validate(row) for row in rows]


@router.post("/papers/favorites", response_model=SemanticScholarFavoriteRead)
def save_search_favorite(
    payload: SemanticScholarFavoriteCreate,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SemanticScholarFavoriteRead:
    row = PaperSearchService(db).upsert_favorite(
        paper=payload.paper.model_dump(by_alias=True),
        note=payload.note,
        actor_id=user_id,
    )
    return SemanticScholarFavoriteRead.model_validate(row)


@router.delete("/papers/favorites/{paper_id}")
def delete_search_favorite(
    paper_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    if not PaperSearchService(db).delete_favorite(paper_id, actor_id=user_id):
        raise HTTPException(status_code=404, detail={"error": "favorite_not_found"})
    return {"deleted": True}


@router.post(
    "/workspaces/{workspace_id}/papers/import-from-s2",
    response_model=PaperRead,
    response_model_exclude_unset=True,
)
def import_external_paper(
    workspace_id: str,
    payload: SemanticScholarImportRequest,
    _owner_id: str = Depends(get_owned_workspace),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
    client: SemanticScholarClient = Depends(_get_semantic_scholar_client),
) -> PaperRead:
    """Import Semantic Scholar metadata into a workspace.

    Import metadata and, when requested, download the advertised open-access
    PDF. PDF processing continues through the existing Celery pipeline.
    """
    workspace_service.get(workspace_id)

    external_id = payload.semantic_scholar_paper_id.strip()
    existing = service.find_by_external_paper_id(
        workspace_id=workspace_id,
        external_paper_id=external_id,
    )
    # Keep imports idempotent when a PDF already exists. If only metadata was
    # imported previously, continue below so the user can retry OA download.
    if existing is not None and (
        existing.primary_artifact_id is not None or not payload.download_open_access_pdf
    ):
        return PaperRead.model_validate(existing)

    raw = client.get_paper(external_id, fields=S2_IMPORT_FIELDS)

    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "semantic_scholar_invalid_paper",
                "message": "Semantic Scholar paper has no usable title",
            },
        )

    authors = [
        author.get("name", "").strip()
        for author in raw.get("authors", [])
        if isinstance(author, dict) and isinstance(author.get("name"), str) and author.get("name", "").strip()
    ]
    external_ids = raw.get("externalIds")
    if not isinstance(external_ids, dict):
        external_ids = {}

    doi = _external_id_as_string(external_ids, "DOI")
    arxiv_id = _external_id_as_string(external_ids, "ArXiv", "ARXIV")
    year = raw.get("year") if isinstance(raw.get("year"), int) else None
    if existing is None:
        paper = service.create_from_metadata(
            workspace_id=workspace_id,
            payload=PaperCreate(
                title=title.strip(),
                authors=authors,
                year=year,
                abstract=raw.get("abstract") if isinstance(raw.get("abstract"), str) else None,
                doi=doi,
                arxiv_id=arxiv_id,
            ),
            source="semantic_scholar",
            external_paper_id=external_id,
        )
    else:
        paper = existing
    if payload.download_open_access_pdf:
        open_access_pdf = raw.get("openAccessPdf")
        pdf_url = (
            open_access_pdf.get("url")
            if isinstance(open_access_pdf, dict)
            else None
        )
        download_source = "semantic_scholar"
        if not isinstance(pdf_url, str) or not pdf_url.strip():
            pdf_url = _arxiv_pdf_url(external_ids)
            download_source = "arxiv_fallback"
        if isinstance(pdf_url, str) and pdf_url.strip():
            try:
                try:
                    content = client.download_pdf(pdf_url.strip())
                except AttributeError as exc:
                    # Keep metadata import graceful for lightweight test or
                    # custom clients that do not implement PDF downloading.
                    from app.gateway.semantic_scholar import SemanticScholarError as _S2Err
                    raise _S2Err(
                        "PDF downloader is unavailable", status_code=502
                    ) from exc
                filename = re.sub(r"[^A-Za-z0-9._-]+", "_", title.strip())[:120]
                service.attach_pdf_to_existing(
                    workspace_id=workspace_id,
                    paper_id=paper.id,
                    filename=f"{filename or external_id}.pdf",
                    content=content,
                    mime_type="application/pdf",
                )
                paper = service.get(paper.id)
            except SemanticScholarError as exc:
                logger.warning(
                    "semantic_scholar.open_access_download_failed",
                    paper_id=paper.id,
                    source=download_source,
                    error=str(exc),
                )
    return PaperRead.model_validate(paper)


@router.post(
    "/workspaces/{workspace_id}/papers/upload",
    response_model=PaperRead,
    status_code=status.HTTP_201_CREATED,
    response_model_exclude_unset=True,
)
async def upload_paper(
    workspace_id: str,
    file: UploadFile = File(...),
    _owner_id: str = Depends(get_owned_workspace),
    title: str | None = Form(None),
    authors: str | None = Form(None),
    year: int | None = Form(None),
    abstract: str | None = Form(None),
    doi: str | None = Form(None),
    arxiv_id: str | None = Form(None),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    workspace_service.get(workspace_id)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_file", "message": "A .pdf file is required"},
        )

    content = await _read_pdf_upload(file)

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
        # The client MIME type is advisory; the helper already checked the
        # PDF signature, so persist and serve the authoritative type.
        mime_type="application/pdf",
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
    _owner_id: str = Depends(get_owned_workspace),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    """Attach a PDF to an existing metadata-only paper.

    Use case: paper was created via `POST /papers` (metadata only), and the
    user later obtains the PDF. Any empty metadata fields on the paper row
    are best-effort filled from the PDF's embedded metadata.
    """
    workspace_service.get(workspace_id)

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_file", "message": "A .pdf file is required"},
        )
    content = await _read_pdf_upload(file)

    paper = service.attach_pdf_to_existing(
        workspace_id=workspace_id,
        paper_id=paper_id,
        filename=file.filename,
        content=content,
        mime_type="application/pdf",
    )
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
    _owner_id: str = Depends(get_owned_workspace),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    workspace_service.get(workspace_id)
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
    _owner_id: str = Depends(get_owned_workspace),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperListResponse:
    workspace_service.get(workspace_id)
    items, total = service.list(workspace_id=workspace_id, limit=limit, offset=offset)
    return PaperListResponse(
        items=[PaperRead.model_validate(p) for p in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/workspaces/{workspace_id}/papers/{paper_id}/extract",
    status_code=status.HTTP_202_ACCEPTED,
)
def trigger_paper_extraction(
    workspace_id: str,
    paper_id: str,
    _owner_id: str = Depends(get_owned_workspace),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> dict[str, str]:
    """Idempotently trigger or retry extraction for a parsed paper."""
    workspace_service.get(workspace_id)
    paper = service.get(paper_id)
    if paper.workspace_id != workspace_id:
        from app.domains.paper.service import PaperNotFoundError
        raise PaperNotFoundError(paper_id)
    if not paper.parsed_markdown_artifact_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "paper_not_parsed",
                "message": "Paper must have parsed_markdown before extraction.",
            },
        )

    from app.workers.tasks.extract_knowledge import spawn_extract_knowledge

    task_id = spawn_extract_knowledge(service.db, paper.id, workspace_id)
    return {"task_id": task_id, "status": "queued"}


@router.get(
    "/workspaces/{workspace_id}/papers/{paper_id}",
    response_model=PaperRead,
    response_model_exclude_unset=True,
)
def get_paper(
    workspace_id: str,
    paper_id: str,
    _owner_id: str = Depends(get_owned_workspace),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    workspace_service.get(workspace_id)
    paper = service.get(paper_id)
    if paper.workspace_id != workspace_id:
        from app.domains.paper.service import PaperNotFoundError
        raise PaperNotFoundError(paper_id)
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
    _owner_id: str = Depends(get_owned_workspace),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> PaperRead:
    workspace_service.get(workspace_id)
    paper = service.get(paper_id)
    if paper.workspace_id != workspace_id:
        from app.domains.paper.service import PaperNotFoundError
        raise PaperNotFoundError(paper_id)
    paper = service.update(paper_id, payload)
    return PaperRead.model_validate(paper)


@router.delete(
    "/workspaces/{workspace_id}/papers/{paper_id}",
    status_code=status.HTTP_200_OK,
)
def delete_paper(
    workspace_id: str,
    paper_id: str,
    _owner_id: str = Depends(get_owned_workspace),
    service: PaperService = Depends(_get_paper_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> dict[str, str | bool]:
    workspace_service.get(workspace_id)
    paper = service.get(paper_id)
    if paper.workspace_id != workspace_id:
        from app.domains.paper.service import PaperNotFoundError
        raise PaperNotFoundError(paper_id)
    service.soft_delete(paper_id)
    return {"id": paper_id, "deleted": True}


# ----------------------------------------------------------------- helpers
def _external_id_as_string(external_ids: dict[str, object], *names: str) -> str | None:
    """Read a string-valued external ID without depending on key casing."""
    for name in names:
        value = external_ids.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _stem(filename: str) -> str:
    """Return the filename without extension, used as a default paper title."""
    import os

    return os.path.splitext(filename)[0] or filename


def _split_authors(raw: str | None) -> list[str]:
    """Split a comma-or-newline-separated author string into a list."""
    if not raw:
        return []
    return [a.strip() for a in raw.replace("\n", ",").split(",") if a.strip()]
