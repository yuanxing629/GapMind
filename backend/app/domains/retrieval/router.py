"""Retrieval HTTP API router.

Endpoints (per api_reference.md "Retrieval（计划）"):
  POST /api/v1/workspaces/{wid}/retrieval/search            semantic search
  POST /api/v1/workspaces/{wid}/retrieval/similar-work      find similar work for a paper
  POST /api/v1/workspaces/{wid}/retrieval/counter-evidence  find counter-evidence for a claim
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.deps import get_owned_workspace
from app.domains.retrieval.schemas import RetrievalResponse
from app.domains.retrieval.service import (
    find_counter_evidence,
    find_similar_work,
    semantic_search,
)

router = APIRouter(
    prefix="/workspaces/{workspace_id}/retrieval",
    tags=["retrieval"],
    dependencies=[Depends(get_owned_workspace)],
)


# ------------------------------------------------------------------
# Request schemas
# ------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)
    section: str | None = None
    exclude_paper_ids: list[str] = Field(
        default_factory=list,
        description="Paper UUIDs to exclude from recall (pushed into the Milvus filter).",
    )
    use_reranker: bool = True


class SimilarWorkRequest(BaseModel):
    paper_id: str
    top_k: int = Field(default=10, ge=1, le=50)
    exclude_paper_ids: list[str] = Field(
        default_factory=list,
        description="Additional paper UUIDs to exclude. The source ``paper_id`` is always excluded.",
    )
    use_reranker: bool = True


class CounterEvidenceRequest(BaseModel):
    claim_text: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)
    source_paper_id: str | None = Field(
        default=None,
        description="UUID of the claim's source paper. Always excluded from recall.",
    )
    exclude_paper_ids: list[str] = Field(
        default_factory=list,
        description="Additional paper UUIDs to exclude. Merged with source_paper_id.",
    )
    use_reranker: bool = True
    use_judge: bool = True


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------


@router.post("/search", response_model=RetrievalResponse)
def api_semantic_search(workspace_id: str, body: SearchRequest) -> RetrievalResponse:
    """Semantic search over workspace paper chunks."""
    result = semantic_search(
        workspace_id=workspace_id,
        query=body.query,
        top_k=body.top_k,
        section=body.section,
        exclude_paper_ids=set(body.exclude_paper_ids) or None,
        use_reranker=body.use_reranker,
    )
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.error or "Retrieval failed")
    return result


@router.post("/similar-work", response_model=RetrievalResponse)
def api_similar_work(workspace_id: str, body: SimilarWorkRequest) -> RetrievalResponse:
    """Find similar work from other papers in the workspace."""
    result = find_similar_work(
        workspace_id=workspace_id,
        paper_id=body.paper_id,
        top_k=body.top_k,
        exclude_paper_ids=set(body.exclude_paper_ids) or None,
        use_reranker=body.use_reranker,
    )
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.error or "Retrieval failed")
    return result


@router.post("/counter-evidence", response_model=RetrievalResponse)
def api_counter_evidence(workspace_id: str, body: CounterEvidenceRequest) -> RetrievalResponse:
    """Find counter-evidence for a claim (reranked + LLM judged)."""
    # The claim's source paper must never be returned as its own counter-evidence.
    excluded = set(body.exclude_paper_ids)
    if body.source_paper_id:
        excluded.add(body.source_paper_id)
    result = find_counter_evidence(
        workspace_id=workspace_id,
        claim_text=body.claim_text,
        top_k=body.top_k,
        exclude_paper_ids=excluded or None,
        use_reranker=body.use_reranker,
        use_judge=body.use_judge,
    )
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.error or "Retrieval failed")
    return result
