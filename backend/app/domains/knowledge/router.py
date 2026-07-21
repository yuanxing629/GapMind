"""Knowledge HTTP API router (read-only for Phase 1b).

Endpoints:
  GET /api/v1/workspaces/{wid}/knowledge            list items (filter by type/status)
  GET /api/v1/workspaces/{wid}/knowledge/{kid}      get one item
  GET /api/v1/workspaces/{wid}/knowledge/{kid}/evidence   list evidence spans
  GET /api/v1/workspaces/{wid}/knowledge/relations  list relations (filter by item_id)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.domains.knowledge.schemas import (
    EvidenceSpanListResponse,
    EvidenceSpanRead,
    KnowledgeItemListResponse,
    KnowledgeItemRead,
    KnowledgeRelationListResponse,
    KnowledgeRelationRead,
)
from app.domains.knowledge.service import (
    KnowledgeItemNotFoundError,
    KnowledgeService,
)
from app.domains.workspace.service import WorkspaceNotFoundError, WorkspaceService

router = APIRouter(tags=["knowledge"])


def _get_knowledge_service(db: Session = Depends(get_db)) -> KnowledgeService:
    return KnowledgeService(db)


def _get_workspace_service(db: Session = Depends(get_db)) -> WorkspaceService:
    return WorkspaceService(db)


def _not_found(exc: Exception) -> HTTPException:
    if isinstance(exc, KnowledgeItemNotFoundError):
        code = "knowledge_item_not_found"
    else:
        code = "workspace_not_found"
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": code, "message": str(exc)},
    )


@router.get(
    "/workspaces/{workspace_id}/knowledge",
    response_model=KnowledgeItemListResponse,
    response_model_exclude_unset=True,
)
def list_knowledge(
    workspace_id: str,
    type: str | None = Query(None),  # noqa: A002  (shadowing builtin is fine in query)
    status: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> KnowledgeItemListResponse:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    items, total = service.list_items(
        workspace_id=workspace_id,
        type_filter=type,
        status_filter=status,
        limit=limit,
        offset=offset,
    )
    return KnowledgeItemListResponse(
        items=[KnowledgeItemRead.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


# IMPORTANT: this route MUST be declared BEFORE /knowledge/{item_id} so
# FastAPI does not match the literal "relations" as an item_id.
@router.get(
    "/workspaces/{workspace_id}/knowledge/relations",
    response_model=KnowledgeRelationListResponse,
    response_model_exclude_unset=True,
)
def list_relations(
    workspace_id: str,
    item_id: str | None = Query(None),
    relation_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> KnowledgeRelationListResponse:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    items, total = service.list_relations(
        workspace_id=workspace_id,
        item_id=item_id,
        relation_type=relation_type,
        limit=limit,
        offset=offset,
    )
    return KnowledgeRelationListResponse(
        items=[KnowledgeRelationRead.model_validate(r) for r in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/workspaces/{workspace_id}/knowledge/{item_id}",
    response_model=KnowledgeItemRead,
    response_model_exclude_unset=True,
)
def get_knowledge_item(
    workspace_id: str,
    item_id: str,
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> KnowledgeItemRead:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    try:
        item = service.get_item(item_id)
    except KnowledgeItemNotFoundError as e:
        raise _not_found(e) from e
    if item.workspace_id != workspace_id:
        raise _not_found(KnowledgeItemNotFoundError(item_id))
    return KnowledgeItemRead.model_validate(item)


@router.get(
    "/workspaces/{workspace_id}/knowledge/{item_id}/evidence",
    response_model=EvidenceSpanListResponse,
    response_model_exclude_unset=True,
)
def list_evidence(
    workspace_id: str,
    item_id: str,
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> EvidenceSpanListResponse:
    try:
        workspace_service.get(workspace_id)
    except WorkspaceNotFoundError as e:
        raise _not_found(e) from e
    try:
        item = service.get_item(item_id)
    except KnowledgeItemNotFoundError as e:
        raise _not_found(e) from e
    if item.workspace_id != workspace_id:
        raise _not_found(KnowledgeItemNotFoundError(item_id))
    spans = service.list_evidence_for_item(item_id)
    return EvidenceSpanListResponse(
        items=[EvidenceSpanRead.model_validate(s) for s in spans],
        total=len(spans),
    )
