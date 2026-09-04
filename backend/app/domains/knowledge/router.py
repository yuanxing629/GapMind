"""Knowledge HTTP API 路由（Phase 1b 只读）。

端点：
  GET /api/v1/workspaces/{wid}/knowledge：列出条目（按 type/status 过滤）
  GET /api/v1/workspaces/{wid}/knowledge/{kid}：获取单个条目
  GET /api/v1/workspaces/{wid}/knowledge/{kid}/evidence：列出 evidence span
  GET /api/v1/workspaces/{wid}/knowledge/relations：列出关系（按 item_id 过滤）

这里抛出的 domain exception 会由 ``app.core.exception_handlers`` 注册的中央
handler 转换为 HTTP 响应。以下两类异常例外处理：

* 跨 workspace 的 404（有意抛出 ``KnowledgeItemNotFoundError``，避免泄露条目存在性）；
* 与本地 artifact 相关、但不属于 domain exception class 的 ``evidence_source_*`` 问题。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_owned_workspace
from app.domains.knowledge.schemas import (
    EvidenceContextRead,
    EvidenceSpanListResponse,
    EvidenceSpanRead,
    ExtractionRejectionListResponse,
    ExtractionRejectionRead,
    KnowledgeGraphResponse,
    KnowledgeGraphSearchResponse,
    KnowledgeItemListResponse,
    KnowledgeItemRead,
    KnowledgeItemReview,
    KnowledgeRelationListResponse,
    KnowledgeRelationRead,
)
from app.domains.knowledge.service import (
    ExtractionRunNotFoundError,
    KnowledgeItemNotFoundError,
    KnowledgeService,
)
from app.domains.workspace.service import WorkspaceService

router = APIRouter(
    tags=["knowledge"],
    dependencies=[Depends(get_owned_workspace)],
)


def _get_knowledge_service(db: Session = Depends(get_db)) -> KnowledgeService:
    return KnowledgeService(db)


def _get_workspace_service(db: Session = Depends(get_db)) -> WorkspaceService:
    return WorkspaceService(db)


@router.get(
    "/workspaces/{workspace_id}/extraction-runs/{run_id}/rejections",
    response_model=ExtractionRejectionListResponse,
)
def list_extraction_rejections(
    workspace_id: str,
    run_id: str,
    kind: str | None = Query(None),
    stage: str | None = Query(None),
    reason_code: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> ExtractionRejectionListResponse:
    workspace_service.get(workspace_id)
    run = service.get_extraction_run(run_id)
    if run.workspace_id != workspace_id:
        raise ExtractionRunNotFoundError(run_id)

    items, total = service.list_rejections(
        workspace_id=workspace_id,
        extraction_run_id=run_id,
        kind_filter=kind,
        stage_filter=stage,
        reason_code_filter=reason_code,
        limit=limit,
        offset=offset,
    )
    return ExtractionRejectionListResponse(
        items=[ExtractionRejectionRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
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
    paper_id: str | None = Query(None),
    q: str | None = Query(None, max_length=255),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> KnowledgeItemListResponse:
    workspace_service.get(workspace_id)
    items, total = service.list_items(
        workspace_id=workspace_id,
        type_filter=type,
        status_filter=status,
        paper_id=paper_id,
        query_text=q,
        min_confidence=min_confidence,
        limit=limit,
        offset=offset,
    )
    return KnowledgeItemListResponse(
        items=[KnowledgeItemRead.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


# 重要：该路由必须声明在 /knowledge/{item_id} 之前，避免 FastAPI 将字面量
# "relations" 匹配为 item_id。
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
    workspace_service.get(workspace_id)
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
    "/workspaces/{workspace_id}/knowledge/graph",
    response_model=KnowledgeGraphResponse,
    response_model_exclude_unset=True,
)
def get_knowledge_graph(
    workspace_id: str,
    type: str | None = Query(None),  # noqa: A002
    paper_id: str | None = Query(None),
    q: str | None = Query(None, max_length=255),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    relation_type: str | None = Query(None),
    status: str | None = Query(None),
    projection_mode: str = Query("all", pattern="^(all|workspace|landscape|claims|evidence)$"),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    edge_limit: int = Query(160, ge=1, le=400),
    include_related_papers: bool = Query(False),
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> KnowledgeGraphResponse:
    """返回自包含、按 workspace 限定的 graph 投影。"""
    workspace_service.get(workspace_id)

    projection = service.graph_projection(
        workspace_id=workspace_id,
        type_filter=type,
        paper_id=paper_id,
        query_text=q,
        min_confidence=min_confidence,
        relation_type=relation_type,
        status_filter=status,
        projection_mode=projection_mode,
        limit=limit,
        offset=offset,
        edge_limit=edge_limit,
        include_related_papers=include_related_papers,
    )
    return KnowledgeGraphResponse(
        workspace_id=workspace_id,
        nodes=projection.nodes,
        edges=projection.edges,
        total_nodes=projection.total_nodes,
        total_edges=projection.total_edges,
        truncated=projection.truncated,
        limit=limit,
        offset=offset,
        projection_mode=projection_mode,
        loaded_nodes=len(projection.nodes),
        loaded_edges=len(projection.edges),
        has_more=projection.has_more,
        node_counts=projection.node_counts,
        relation_counts=projection.relation_counts,
        workspace_counts=projection.workspace_counts,
        truncation_reason=projection.truncation_reason,
    )


@router.get(
    "/workspaces/{workspace_id}/knowledge/graph/search",
    response_model=KnowledgeGraphSearchResponse,
)
def search_knowledge_graph_nodes(
    workspace_id: str,
    q: str = Query(..., min_length=1, max_length=255),
    projection_mode: str = Query("all", pattern="^(all|workspace|landscape|claims|evidence)$"),
    limit: int = Query(12, ge=1, le=50),
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> KnowledgeGraphSearchResponse:
    workspace_service.get(workspace_id)
    return KnowledgeGraphSearchResponse(
        items=service.search_graph_nodes(
            workspace_id=workspace_id,
            query_text=q,
            projection_mode=projection_mode,
            limit=limit,
        )
    )


@router.get(
    "/workspaces/{workspace_id}/knowledge/graph/neighbors/{node_id}",
    response_model=KnowledgeGraphResponse,
    response_model_exclude_unset=True,
)
def get_knowledge_graph_neighbors(
    workspace_id: str,
    node_id: str,
    depth: int = Query(1, ge=1, le=2),
    limit: int = Query(100, ge=1, le=200),
    relation_type: str | None = Query(None),
    projection_mode: str = Query("all", pattern="^(all|workspace|landscape|claims|evidence)$"),
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> KnowledgeGraphResponse:
    workspace_service.get(workspace_id)
    if projection_mode == "workspace":
        projection = service.workspace_graph_projection(
            workspace_id=workspace_id,
            relation_type=relation_type,
            node_limit=limit,
            edge_limit=min(400, limit * 2),
            focus_node_id=node_id,
            focus_depth=depth,
        )
        return KnowledgeGraphResponse(
            workspace_id=workspace_id,
            nodes=projection.nodes,
            edges=projection.edges,
            total_nodes=projection.total_nodes,
            total_edges=projection.total_edges,
            truncated=projection.truncated,
            limit=limit,
            offset=0,
            projection_mode="workspace",
            loaded_nodes=len(projection.nodes),
            loaded_edges=len(projection.edges),
            has_more=projection.has_more,
            node_counts=projection.node_counts,
            relation_counts=projection.relation_counts,
            workspace_counts=projection.workspace_counts,
            seed_node_id=node_id,
            depth=depth,
            truncation_reason=projection.truncation_reason,
        )

    nodes, edges = service.graph_neighbors(
        workspace_id=workspace_id,
        node_id=node_id,
        depth=depth,
        limit=limit,
        relation_type=relation_type,
    )
    return KnowledgeGraphResponse(
        workspace_id=workspace_id,
        nodes=nodes,
        edges=edges,
        total_nodes=len(nodes),
        total_edges=len(edges),
        limit=limit,
        offset=0,
        projection_mode="neighbors",
        loaded_nodes=len(nodes),
        loaded_edges=len(edges),
        has_more=False,
        seed_node_id=node_id,
        depth=depth,
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
    workspace_service.get(workspace_id)
    item = service.get_item(item_id, workspace_id=workspace_id)
    return KnowledgeItemRead.model_validate(item)


@router.patch(
    "/workspaces/{workspace_id}/knowledge/{item_id}/review",
    response_model=KnowledgeItemRead,
    response_model_exclude_unset=True,
)
def review_knowledge_item(
    workspace_id: str,
    item_id: str,
    payload: KnowledgeItemReview,
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> KnowledgeItemRead:
    workspace_service.get(workspace_id)
    item = service.review_item(
        workspace_id=workspace_id, item_id=item_id, payload=payload
    )
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
    workspace_service.get(workspace_id)
    item = service.get_item(item_id, workspace_id=workspace_id)
    spans = service.list_evidence_for_item(item_id, workspace_id=workspace_id)
    return EvidenceSpanListResponse(
        items=[EvidenceSpanRead.model_validate(s) for s in spans],
        total=len(spans),
    )


@router.get(
    "/workspaces/{workspace_id}/knowledge/{item_id}/evidence/context",
    response_model=EvidenceContextRead,
)
def get_evidence_context(
    workspace_id: str,
    item_id: str,
    evidence_span_id: str | None = Query(None, min_length=1),
    db: Session = Depends(get_db),
    service: KnowledgeService = Depends(_get_knowledge_service),
    workspace_service: WorkspaceService = Depends(_get_workspace_service),
) -> EvidenceContextRead:
    from app.domains.artifact.models import Artifact
    from app.domains.artifact.service import ArtifactService
    from app.domains.paper.models import Paper

    workspace_service.get(workspace_id)
    item = service.get_item(item_id, workspace_id=workspace_id)

    spans = service.list_evidence_for_item(item_id, workspace_id=workspace_id)
    selected_span = None
    if evidence_span_id is not None:
        selected_span = next((span for span in spans if str(span.id) == str(evidence_span_id)), None)
        if selected_span is None or str(selected_span.paper_id) != str(item.paper_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "evidence_source_not_found",
                    "message": "Evidence span is not available for this knowledge item",
                },
            )
        spans = [selected_span]

    artifact_id = (
        selected_span.artifact_id
        if selected_span is not None
        else next((span.artifact_id for span in spans if span.artifact_id), None)
    )
    paper_id = (
        selected_span.paper_id
        if selected_span is not None
        else next((span.paper_id for span in spans if span.paper_id), None)
    ) or item.paper_id
    if artifact_id is None and paper_id:
        paper = db.scalar(
            select(Paper).where(
                Paper.id == paper_id,
                Paper.workspace_id == workspace_id,
                Paper.is_deleted.is_(False),
            )
        )
        artifact_id = paper.parsed_markdown_artifact_id if paper else None
    if not artifact_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "evidence_source_not_found",
                "message": "No parsed markdown artifact is linked to this item",
            },
        )
    artifact = db.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.workspace_id == workspace_id,
            Artifact.is_deleted.is_(False),
        )
    )
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "evidence_source_not_found", "message": "Evidence artifact not found"},
        )
    path = ArtifactService(db).resolve_abs_path(artifact)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "evidence_source_missing", "message": "Evidence artifact is missing on disk"},
        )
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "evidence_source_read_failed", "message": str(exc)},
        ) from exc
    return EvidenceContextRead(
        workspace_id=workspace_id,
        paper_id=paper_id or "",
        artifact_id=artifact.id,
        artifact_kind=artifact.kind,
        filename=artifact.original_filename,
        content=content,
        spans=[EvidenceSpanRead.model_validate(span) for span in spans],
    )
