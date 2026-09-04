"""Retrieval HTTP API 路由。

Endpoints（依据 api_reference.md "Retrieval（计划）"）：
  POST /api/v1/workspaces/{wid}/retrieval/search            语义搜索
  POST /api/v1/workspaces/{wid}/retrieval/similar-work      查找论文的相似工作
  POST /api/v1/workspaces/{wid}/retrieval/counter-evidence  查找 claim 的 counter-evidence
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_owned_workspace
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
# 请求 schemas
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
# 端点
# ------------------------------------------------------------------


@router.post("/search", response_model=RetrievalResponse)
def api_semantic_search(workspace_id: str, body: SearchRequest) -> RetrievalResponse:
    """在 workspace 论文分块上执行语义搜索。"""
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
def api_similar_work(
    workspace_id: str,
    body: SimilarWorkRequest,
    db: Session = Depends(get_db),
) -> RetrievalResponse:
    """从 workspace 的其他论文中查找相似工作。"""
    result = find_similar_work(
        workspace_id=workspace_id,
        paper_id=body.paper_id,
        top_k=body.top_k,
        db=db,
        exclude_paper_ids=set(body.exclude_paper_ids) or None,
        use_reranker=body.use_reranker,
    )
    if result.status == "failed":
        raise HTTPException(status_code=502, detail=result.error or "Retrieval failed")
    return result


@router.post("/counter-evidence", response_model=RetrievalResponse)
def api_counter_evidence(workspace_id: str, body: CounterEvidenceRequest) -> RetrievalResponse:
    """查找 claim 的 counter-evidence（重排序 + LLM 判断）。"""
# claim 的源论文永远不能作为自身的 counter-evidence 返回。
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
