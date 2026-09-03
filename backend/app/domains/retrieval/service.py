"""Retrieval service layer - chunk indexing and semantic search.

Step ④: Read chunks JSONL (Contract B) → embed via BGE-M3 → insert Milvus.
Step ⑤: semantic_search / find_similar_work / find_counter_evidence (Phase 3).

Pipeline stages (Contract D retrieval_stage):
  candidate_recall → reranked → llm_judged
"""

from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.artifact.models import Artifact
from app.domains.artifact.service import ArtifactService
from app.domains.paper.models import Paper
from app.domains.retrieval import milvus_client
from app.domains.retrieval.schemas import (
    ChunkRecord,
    IndexChunksResult,
    RetrievalResponse,
    RetrievalResultItem,
)
from app.gateway.embedding import get_embedding_gateway
from app.gateway.judge import get_judgement_gateway
from app.gateway.reranker import get_reranker_gateway

logger = get_logger(__name__)

# Similar Work aggregation: at most this many chunks per paper inside the top-K.
# The pipeline over-fetches from Milvus, so this is a cap on the *post-aggregation*
# candidate pool, not on the raw Milvus recall — recall quality is preserved.
SIMILAR_WORK_MAX_CHUNKS_PER_PAPER = 2

# Counter Evidence aggregation: a tighter cap. Counter evidence per claim is
# usually concentrated in a handful of papers; capping prevents one paper's
# multiple contradicting chunks from crowding out the rare genuinely
# qualifying hit from a different paper.
COUNTER_EVIDENCE_MAX_CHUNKS_PER_PAPER = 3

# Role priority for sorting Counter Evidence results. Lower number = higher
# priority. Counter-evidence search's value is in contradicts / qualifies;
# overlaps and supports are admitted only to be explicit about absence.
COUNTER_ROLE_PRIORITY: dict[str, int] = {
    "contradicts": 0,
    "qualifies": 1,
    "supports": 2,
    "overlaps": 2,
    "unknown": 3,
}

# Sections whose presence is rarely the answer to "is this paper similar work".
# These typically cite the related work rather than describe it — keeping them
# in the candidate pool causes a small set of high-citation papers to dominate
# the Top-K purely through reference density.
LOW_VALUE_SECTIONS: frozenset[str] = frozenset({
    "references",
    "bibliography",
    "acknowledgments",
    "acknowledgements",
    "appendix",
    "author contributions",
    "supplementary",
    "supplementary material",
})

# Keep this mapping intentionally small and user-facing.  Provider/Milvus
# exception strings are logged for operators, but never returned as an API
# diagnostic because they can expose topology or credential-related details.
RETRIEVAL_DIAGNOSTIC_MESSAGES: dict[str, str] = {
    "embedding_unavailable": "工作区论文检索暂时无法生成查询向量。请检查 embedding API Key、服务地址和网络后重试。",
    "milvus_unavailable": "工作区论文检索暂时无法连接向量库。请检查 Milvus、etcd 和 minio 基础设施后重试。",
    "collection_unloaded": "论文向量集合尚未加载。请重新加载 collection 后重试；当前不需要直接重建索引。",
    "reranker_degraded": "重排服务暂时不可用，已降级使用向量召回结果。可先查看当前结果，恢复重排服务后重试。",
    "unknown": "工作区论文检索失败：遇到未分类故障。请稍后重试，并查看后端诊断日志。",
}


def _diagnostic_message(code: str) -> str:
    return RETRIEVAL_DIAGNOSTIC_MESSAGES.get(code, RETRIEVAL_DIAGNOSTIC_MESSAGES["unknown"])


def _classify_failure(exc: Exception, *, stage: str) -> str:
    """Map a retrieval-stage failure to a stable, non-sensitive code."""

    if stage == "embedding":
        return "embedding_unavailable"
    if stage == "reranker":
        return "reranker_degraded"

    raw = f"{type(exc).__name__}: {exc}".lower()
    if stage == "milvus":
        if "collection" in raw and any(
            marker in raw for marker in ("not loaded", "unloaded", "load state", "load collection")
        ):
            return "collection_unloaded"
        return "milvus_unavailable"
    if "embedding" in raw or "siliconflow" in raw or "api key" in raw:
        return "embedding_unavailable"
    if "collection" in raw and any(
        marker in raw for marker in ("not loaded", "unloaded", "load state")
    ):
        return "collection_unloaded"
    if "milvus" in raw or "pymilvus" in raw or "grpc" in raw:
        return "milvus_unavailable"
    return "unknown"


def _failed_response(
    *,
    request_id: str,
    workspace_id: str,
    query: str,
    purpose: str,
    start_time: float,
    diagnostic_code: str,
    filters_applied: dict | None = None,
) -> RetrievalResponse:
    latency = (time.perf_counter() - start_time) * 1000
    return RetrievalResponse(
        request_id=request_id,
        workspace_id=workspace_id,
        query=query,
        purpose=purpose,
        status="failed",
        latency_ms=round(latency, 2),
        error=_diagnostic_message(diagnostic_code),
        diagnostic_code=diagnostic_code,
        filters_applied=filters_applied or {},
    )


# ==================================================================
# Step ④: Index paper chunks into Milvus
# ==================================================================


def index_paper_chunks(
    workspace_id: str,
    paper_id: str,
    *,
    db: Session,
    force_reindex: bool = False,
) -> IndexChunksResult:
    """Main entry: load the chunk_index Artifact → embed → insert into Milvus.

    Idempotent: skips chunks already indexed unless force_reindex=True.
    """
    start_time = time.perf_counter()
    gateway = get_embedding_gateway()

    result = IndexChunksResult(
        workspace_id=workspace_id,
        paper_id=paper_id,
        embedding_model=gateway.model,
        embedding_dim=gateway.dim,
    )

    # 1. Load chunks from JSONL
    chunks = _load_chunks_jsonl(db, workspace_id, paper_id)
    if not chunks:
        result.error = f"No chunks found for paper {paper_id}"
        logger.warning("index.no_chunks", workspace_id=workspace_id, paper_id=paper_id)
        return result

    result.total_chunks = len(chunks)

    # 2. Idempotency: skip already-indexed chunks
    if force_reindex:
        milvus_client.delete_by_paper(paper_id)
        to_index = chunks
    else:
        existing_ids = milvus_client.get_existing_chunk_ids(paper_id)
        to_index = [c for c in chunks if c.chunk_id not in existing_ids]
        result.skipped_count = len(chunks) - len(to_index)

    if not to_index:
        result.indexed_count = 0
        result.duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "index.all_skipped",
            paper_id=paper_id,
            skipped=result.skipped_count,
        )
        return result

    # 3. Embed texts via BGE-M3
    texts = [c.text for c in to_index]
    logger.info(
        "index.embedding_start",
        paper_id=paper_id,
        chunk_count=len(texts),
    )
    embedding_result = gateway.embed_texts(texts)

    # 4. Build Milvus records
    records: list[dict[str, Any]] = []
    for chunk, vector in zip(
        to_index,
        embedding_result.embeddings,
        strict=False,
    ):
        records.append({
            "chunk_id": chunk.chunk_id,
            "workspace_id": chunk.workspace_id,
            "paper_id": chunk.paper_id,
            "source_artifact_id": chunk.source_artifact_id,
            "chunk_index": chunk.chunk_index,
            "section": chunk.section or "Unknown",
            "text": chunk.text[:8000],  # Milvus VARCHAR(8192) safety margin
            "tokens_estimate": chunk.tokens_estimate,
            "embedding": vector,
        })

    # 5. Insert into Milvus (batch to avoid single huge request)
    batch_size = 100
    total_inserted = 0
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        total_inserted += milvus_client.insert_chunks(batch)

    result.indexed_count = total_inserted
    result.duration_ms = (time.perf_counter() - start_time) * 1000

    logger.info(
        "index.completed",
        paper_id=paper_id,
        workspace_id=workspace_id,
        indexed=result.indexed_count,
        skipped=result.skipped_count,
        duration_ms=round(result.duration_ms, 1),
    )
    return result


def _find_chunk_index_artifact(
    db: Session,
    workspace_id: str,
    paper_id: str,
) -> Artifact | None:
    """Resolve a paper's canonical chunk_index Artifact inside its workspace."""
    paper = db.get(Paper, paper_id)
    if paper is None or paper.is_deleted or paper.workspace_id != workspace_id:
        logger.warning(
            "index.paper_not_found_or_wrong_workspace",
            paper_id=paper_id,
            workspace_id=workspace_id,
        )
        return None

    if paper.chunk_index_artifact_id:
        artifact = db.get(Artifact, paper.chunk_index_artifact_id)
        if (
            artifact is not None
            and not artifact.is_deleted
            and artifact.workspace_id == workspace_id
            and artifact.kind == "chunk_index"
        ):
            return artifact
        logger.warning(
            "index.chunk_index_artifact_invalid",
            paper_id=paper_id,
            artifact_id=paper.chunk_index_artifact_id,
        )

    # Compatibility for historical rows created before the Paper pointer was
    # populated. Only search the same workspace and the immutable chunk-index
    # kind; never fall back to an unscoped filesystem path.
    filenames = {
        f"{paper_id}_chunks.jsonl",
        f"{paper_id}_chunks_rebuilt.jsonl",
    }
    candidates = db.execute(
        select(Artifact)
        .where(
            Artifact.workspace_id == workspace_id,
            Artifact.kind == "chunk_index",
            Artifact.is_deleted.is_(False),
            Artifact.original_filename.in_(filenames),
        )
        .order_by(Artifact.created_at.desc())
    ).scalars()
    return next(iter(candidates), None)


def _load_chunks_jsonl(
    db: Session,
    workspace_id: str,
    paper_id: str,
) -> list[ChunkRecord]:
    """Read and validate chunks from the paper's storage Artifact (Contract B)."""
    artifact = _find_chunk_index_artifact(db, workspace_id, paper_id)
    if artifact is None:
        logger.warning(
            "index.chunk_index_artifact_not_found",
            paper_id=paper_id,
            workspace_id=workspace_id,
        )
        return []

    jsonl_path = ArtifactService(db).resolve_abs_path(artifact)
    if not jsonl_path.exists() or not jsonl_path.is_file():
        logger.warning(
            "index.chunk_index_file_not_found",
            paper_id=paper_id,
            workspace_id=workspace_id,
            artifact_id=artifact.id,
            path=str(jsonl_path),
        )
        return []

    chunks: list[ChunkRecord] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                chunk = ChunkRecord.model_validate(raw)
                if chunk.workspace_id != workspace_id or chunk.paper_id != paper_id:
                    logger.warning(
                        "index.chunk_scope_mismatch",
                        artifact_id=artifact.id,
                        line=line_num,
                        expected_workspace_id=workspace_id,
                        expected_paper_id=paper_id,
                        actual_workspace_id=chunk.workspace_id,
                        actual_paper_id=chunk.paper_id,
                    )
                    continue
                chunks.append(chunk)
            except (json.JSONDecodeError, ValidationError) as e:
                logger.warning(
                    "index.invalid_chunk_line",
                    path=str(jsonl_path),
                    line=line_num,
                    error=str(e)[:200],
                )
    return chunks


def find_chunk_record(
    workspace_id: str,
    paper_id: str,
    chunk_id: str,
    *,
    db: Session,
) -> ChunkRecord | None:
    """Resolve a retrieved Milvus hit to immutable offsets in storage."""
    return next(
        (
            chunk
            for chunk in _load_chunks_jsonl(db, workspace_id, paper_id)
            if chunk.chunk_id == chunk_id
        ),
        None,
    )


# ==================================================================
# Step ⑤: Retrieval functions (Contract D output)
# ==================================================================


def semantic_search(
    workspace_id: str,
    query: str,
    top_k: int = 10,
    *,
    section: str | None = None,
    exclude_paper_ids: set[str] | None = None,
    use_reranker: bool = True,
    diversify_by_paper: bool = False,
) -> RetrievalResponse:
    """General semantic search within a workspace.

    Pipeline: vector recall → (optional) rerank → optional paper diversity → return.
    ``exclude_paper_ids`` is pushed into the Milvus filter (see
    ``milvus_client.search``) and surfaced on ``filters_applied``.
    ``diversify_by_paper`` is intended for answer generation: it retains the
    strongest chunk from each paper after reranking so one long paper cannot
    consume every evidence slot. It is opt-in to preserve the public semantic
    search API's chunk-level behaviour.
    """
    start_time = time.perf_counter()
    request_id = str(uuid4())
    stage = "embedding"

    try:
        gateway = get_embedding_gateway()
        filters_applied = {
            "section": section,
            "excluded_paper_ids": sorted(exclude_paper_ids or set()),
            "diversify_by_paper": diversify_by_paper,
            "recall_count": 0,
            "reranker_enabled": use_reranker,
            "reranker_applied": False,
        }
        # Stage 1: Vector recall (over-fetch for reranker)
        recall_k = top_k * 3 if use_reranker else top_k
        query_vector = gateway.embed_one(query)
        stage = "milvus"
        hits = milvus_client.search(
            query_vector,
            workspace_id,
            top_k=recall_k,
            section=section,
            exclude_paper_ids=exclude_paper_ids,
        )

        if not hits:
            latency = (time.perf_counter() - start_time) * 1000
            return RetrievalResponse(
                request_id=request_id,
                workspace_id=workspace_id,
                query=query,
                purpose="semantic",
                status="succeeded",
                items=[],
                total=0,
                latency_ms=round(latency, 2),
                filters_applied=filters_applied,
            )

        # Stage 2: Rerank
        diagnostic_codes: list[str] = []
        filters_applied["recall_count"] = len(hits)
        filters_applied["reranker_applied"] = use_reranker and len(hits) > 1
        if use_reranker and len(hits) > 1:
            items = _rerank_hits(
                query,
                hits,
                recall_k if diversify_by_paper else top_k,
                diagnostic_codes,
            )
        else:
            items = [_hit_to_result_item(hit) for hit in hits]
        if diversify_by_paper:
            items = _paper_max_top_k(items, top_k)
        else:
            items = items[:top_k]

        latency = (time.perf_counter() - start_time) * 1000
        diagnostic_code = diagnostic_codes[0] if diagnostic_codes else None
        return RetrievalResponse(
            request_id=request_id,
            workspace_id=workspace_id,
            query=query,
            purpose="semantic",
            status="degraded" if diagnostic_code else "succeeded",
            items=items,
            total=len(items),
            latency_ms=round(latency, 2),
            filters_applied=filters_applied,
            error=_diagnostic_message(diagnostic_code) if diagnostic_code else None,
            diagnostic_code=diagnostic_code,
        )
    except Exception as e:
        diagnostic_code = _classify_failure(e, stage=stage)
        logger.error(
            "retrieval.semantic_search_failed",
            error=str(e),
            diagnostic_code=diagnostic_code,
        )
        return _failed_response(
            request_id=request_id,
            workspace_id=workspace_id,
            query=query,
            purpose="semantic",
            start_time=start_time,
            diagnostic_code=diagnostic_code,
            filters_applied={
                "section": section,
                "excluded_paper_ids": sorted(exclude_paper_ids or set()),
                "diversify_by_paper": diversify_by_paper,
                "recall_count": 0,
                "reranker_enabled": use_reranker,
                "reranker_applied": False,
            },
        )


def find_similar_work(
    workspace_id: str,
    paper_id: str,
    top_k: int = 10,
    *,
    db: Session,
    use_reranker: bool = True,
    exclude_paper_ids: set[str] | None = None,
) -> RetrievalResponse:
    """Find chunks from other papers that are similar to the given paper.

    Pipeline: multi-vector recall → exclude same paper → (optional) rerank → return.
    Uses the paper's own chunks as queries (multi-vector recall).
    """
    start_time = time.perf_counter()
    request_id = str(uuid4())
    stage = "embedding"

    try:
        gateway = get_embedding_gateway()
        stage = "data"
        # Load representative chunks from the target paper as queries
        chunks = _load_chunks_jsonl(db, workspace_id, paper_id)
        if not chunks:
            return _failed_response(
                request_id=request_id,
                workspace_id=workspace_id,
                query=f"paper:{paper_id}",
                purpose="similar_work",
                start_time=start_time,
                diagnostic_code="unknown",
            )

        # Use up to 5 representative chunks (spread across the paper)
        sample_indices = _spread_sample_indices(len(chunks), max_samples=5)
        query_texts = [chunks[i].text for i in sample_indices]

        # Embed all query chunks
        stage = "embedding"
        embed_result = gateway.embed_texts(query_texts)
        stage = "milvus"

        # Exclusion set: the source paper is ALWAYS excluded (it's "similar
        # work" by definition), plus any caller-supplied papers.
        excluded = set(exclude_paper_ids or set()) | {paper_id}

        # Search for each, collecting hits. Exclusion is pushed down into the
        # Milvus filter so excluded papers never enter the recall pool.
        seen_chunk_ids: set[str] = set()
        all_hits: list[dict[str, Any]] = []

        for vector in embed_result.embeddings:
            hits = milvus_client.search(
                vector,
                workspace_id,
                top_k=top_k * 4,  # over-fetch: low-value section drops + per-paper cap leave gaps
                exclude_paper_ids=excluded,
            )
            for hit in hits:
                # Defensive drop: Milvus's filter should already exclude
                # ``excluded``, but a buggy / partial-implementation should
                # never leak the source paper into "similar work" results.
                if hit.get("paper_id") in excluded:
                    continue
                hit_chunk_id = hit.get("chunk_id", "")
                if hit_chunk_id in seen_chunk_ids:
                    continue
                seen_chunk_ids.add(hit_chunk_id)
                all_hits.append(hit)

        if not all_hits:
            latency = (time.perf_counter() - start_time) * 1000
            return RetrievalResponse(
                request_id=request_id,
                workspace_id=workspace_id,
                query=f"paper:{paper_id}",
                purpose="similar_work",
                status="succeeded",
                items=[],
                total=0,
                latency_ms=round(latency, 2),
                filters_applied={"excluded_paper_ids": sorted(excluded)},
            )

        # Step 1: drop low-value sections (References / Acknowledgments etc.)
        # These rarely contain genuinely "similar work" — they just cite it.
        filtered_hits = [h for h in all_hits if not _is_low_value_section(h.get("section"))]
        if not filtered_hits:
            # All candidates were low-value; fall back to whatever we have.
            filtered_hits = all_hits

        # Step 2: paper-level aggregation + per-paper chunk cap.
        # Group by paper, take the top MAX_CHUNKS_PER_PAPER chunks from each,
        # then re-merge into a single candidate pool ordered by score.
        by_paper: dict[str, list[dict[str, Any]]] = {}
        for hit in filtered_hits:
            pid = hit.get("paper_id") or ""
            by_paper.setdefault(pid, []).append(hit)
        candidates: list[dict[str, Any]] = []
        for pid, hits in by_paper.items():
            hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
            candidates.extend(hits[:SIMILAR_WORK_MAX_CHUNKS_PER_PAPER])

        # Step 3: rerank the diversified candidate pool (or sort by score if reranker disabled).
        diagnostic_codes: list[str] = []
        if use_reranker and len(candidates) > 1:
            rerank_query = query_texts[0][:500]
            # Rerank the whole pool, then blend with the raw vector score and
            # keep the top chunk per paper: the top-k slots then surface k
            # DISTINCT papers, and papers the reranker alone would demote (same
            # topic, different phrasing) stay in contention.
            reranked_all = _rerank_hits(rerank_query, candidates, len(candidates), diagnostic_codes)
            items = _hybrid_rerank_top_k(candidates, reranked_all, top_k)
        else:
            candidates.sort(key=lambda h: h.get("score", 0.0), reverse=True)
            items = _paper_max_top_k(
                [_hit_to_result_item(hit) for hit in candidates], top_k
            )

        latency = (time.perf_counter() - start_time) * 1000
        diagnostic_code = diagnostic_codes[0] if diagnostic_codes else None
        return RetrievalResponse(
            request_id=request_id,
            workspace_id=workspace_id,
            query=f"paper:{paper_id}",
            purpose="similar_work",
            status="degraded" if diagnostic_code else "succeeded",
            items=items,
            total=len(items),
            latency_ms=round(latency, 2),
            filters_applied={
                "excluded_paper_ids": sorted(excluded),
                "low_value_section_filter": True,
                "max_chunks_per_paper": SIMILAR_WORK_MAX_CHUNKS_PER_PAPER,
            },
            error=_diagnostic_message(diagnostic_code) if diagnostic_code else None,
            diagnostic_code=diagnostic_code,
        )
    except Exception as e:
        diagnostic_code = _classify_failure(e, stage=stage)
        logger.error(
            "retrieval.similar_work_failed",
            error=str(e),
            diagnostic_code=diagnostic_code,
        )
        return _failed_response(
            request_id=request_id,
            workspace_id=workspace_id,
            query=f"paper:{paper_id}",
            purpose="similar_work",
            start_time=start_time,
            diagnostic_code=diagnostic_code,
        )


def find_counter_evidence(
    workspace_id: str,
    claim_text: str,
    top_k: int = 10,
    *,
    use_reranker: bool = True,
    use_judge: bool = True,
    exclude_paper_ids: set[str] | None = None,
) -> RetrievalResponse:
    """Find chunks that may contradict or qualify a given claim.

    Pipeline: vector recall → rerank → LLM/NLI judge → return.
    Contract D requirement: counter_evidence MUST pass through rerank or
    LLM/NLI judgement. retrieval_stage = 'llm_judged' when judge is used.
    """
    start_time = time.perf_counter()
    request_id = str(uuid4())
    stage = "embedding"

    try:
        gateway = get_embedding_gateway()
        # Stage 1: Vector recall (over-fetch). Exclusion of the claim's source
        # paper is pushed down into the Milvus filter so the source's own
        # chunks never enter the recall pool — otherwise they would crowd out
        # genuinely countering evidence.
        recall_k = top_k * 3 if (use_reranker or use_judge) else top_k
        query_vector = gateway.embed_one(claim_text)
        stage = "milvus"
        hits = milvus_client.search(
            query_vector,
            workspace_id,
            top_k=recall_k,
            exclude_paper_ids=exclude_paper_ids,
        )
        # Belt-and-suspenders: the Milvus filter is the primary exclusion,
        # but a defensive drop guards against filter-syntax regressions in
        # specific Milvus versions.
        if exclude_paper_ids:
            hits = [hit for hit in hits if hit.get("paper_id") not in exclude_paper_ids]

        if not hits:
            latency = (time.perf_counter() - start_time) * 1000
            return RetrievalResponse(
                request_id=request_id,
                workspace_id=workspace_id,
                query=claim_text,
                purpose="counter_evidence",
                # No Milvus candidates at all. Crucially: this is NOT a system
                # failure — we just couldn't find anything similar enough.
                status="succeeded",
                items=[],
                total=0,
                latency_ms=round(latency, 2),
                filters_applied={"excluded_paper_ids": sorted(exclude_paper_ids or set())},
                empty_reason="retrieval_empty",
            )

        # Stage 2: Rerank the whole recall pool, then keep the top chunk per
        # paper so the top-k slots surface k DISTINCT papers. A single paper's
        # many chunks would otherwise crowd out counter evidence from other
        # papers — and the Gate measures recall at the paper level.
        diagnostic_codes: list[str] = []
        if use_reranker and len(hits) > 1:
            reranked_items = _paper_max_top_k(
                _rerank_hits(claim_text, hits, len(hits), diagnostic_codes), top_k
            )
        else:
            reranked_items = _paper_max_top_k(
                [_hit_to_result_item(hit) for hit in hits], top_k
            )

        # Stage 3: LLM Judgement (NLI classification)
        if use_judge and reranked_items:
            items = _judge_items(claim_text, reranked_items)
        else:
            items = reranked_items

        # Stage 4: paper-diversify + role-priority sort. Counter evidence per
        # claim typically concentrates in a handful of papers; cap each
        # paper's contribution so one paper's many contradicting chunks don't
        # crowd out a single qualifying hit from a different paper.
        items = _diversify_and_sort_counter_items(items)

        # Determine status: judge-failed sentinel (any zero-confidence unknown)
        # pushes the response into "degraded" so the UI can distinguish it
        # from a clean "no counter-evidence found".
        status = "succeeded"
        if diagnostic_codes:
            status = "degraded"
        judge_failed = any(
            item.judgement == "unknown" and item.judgement_confidence == 0.0
            for item in items
        )
        if judge_failed:
            status = "degraded"

        latency = (time.perf_counter() - start_time) * 1000

        # Decide empty_reason. Three mutually exclusive cases the UI must
        # distinguish (see ``CounterEmptyReason``):
        #   1. retrieval_empty              → Milvus returned 0 candidates
        #   2. judge_failed                 → Judge couldn't classify any candidate
        #                                       (zero-confidence unknown sentinel)
        #   3. genuinely_no_counter_evidence → Judge ran but only supports /
        #                                       overlaps / non-zero-confidence unknowns
        #                                       came back — no real counter-evidence
        #                                       exists in the workspace.
        # The fourth case (items contain contradicts/qualifies) sets no
        # empty_reason — the top-K is good as-is.
        empty_reason: str | None = None
        has_counter_role = any(
            item.judgement in ("contradicts", "qualifies") for item in items
        )
        if not items:
            empty_reason = "judge_failed" if judge_failed else "retrieval_empty"
        elif not has_counter_role:
            empty_reason = "judge_failed" if judge_failed else "genuinely_no_counter_evidence"

        return RetrievalResponse(
            request_id=request_id,
            workspace_id=workspace_id,
            query=claim_text,
            purpose="counter_evidence",
            status=status,
            items=items,
            total=len(items),
            latency_ms=round(latency, 2),
            filters_applied={
                "excluded_paper_ids": sorted(exclude_paper_ids or set()),
                "max_chunks_per_paper": COUNTER_EVIDENCE_MAX_CHUNKS_PER_PAPER,
                "role_priority": COUNTER_ROLE_PRIORITY,
            },
            empty_reason=empty_reason,
            error=_diagnostic_message(diagnostic_codes[0]) if diagnostic_codes else None,
            diagnostic_code=diagnostic_codes[0] if diagnostic_codes else None,
        )
    except Exception as e:
        diagnostic_code = _classify_failure(e, stage=stage)
        logger.error(
            "retrieval.counter_evidence_failed",
            error=str(e),
            diagnostic_code=diagnostic_code,
        )
        return _failed_response(
            request_id=request_id,
            workspace_id=workspace_id,
            query=claim_text,
            purpose="counter_evidence",
            start_time=start_time,
            diagnostic_code=diagnostic_code,
            filters_applied={
                "excluded_paper_ids": sorted(exclude_paper_ids or set()),
            },
        )


# ==================================================================
# Internal pipeline stages
# ==================================================================


def _rerank_hits(
    query: str,
    hits: list[dict[str, Any]],
    top_k: int,
    diagnostic_codes: list[str] | None = None,
) -> list[RetrievalResultItem]:
    """Rerank Milvus hits using cross-encoder, return top_k items."""
    reranker = get_reranker_gateway()
    documents = [hit.get("text", "") for hit in hits]

    try:
        rerank_result = reranker.rerank(query, documents, top_n=top_k)
    except Exception as e:
        # Graceful degradation: fall back to vector score ordering
        logger.warning(
            "retrieval.rerank_failed_fallback",
            error=str(e),
            diagnostic_code="reranker_degraded",
        )
        if diagnostic_codes is not None:
            diagnostic_codes.append("reranker_degraded")
        hits_sorted = sorted(hits, key=lambda h: h.get("score", 0.0), reverse=True)
        return [_hit_to_result_item(hit) for hit in hits_sorted[:top_k]]

    # Map reranked indices back to hits
    items: list[RetrievalResultItem] = []
    for rerank_hit in rerank_result.hits[:top_k]:
        if rerank_hit.index < len(hits):
            original_hit = hits[rerank_hit.index]
            item = _hit_to_result_item(original_hit, retrieval_stage="reranked")
            item.score = rerank_hit.relevance_score
            items.append(item)

    return items


def _judge_items(
    claim: str,
    items: list[RetrievalResultItem],
) -> list[RetrievalResultItem]:
    """Apply LLM judgement to reranked items (counter_evidence only)."""
    judge = get_judgement_gateway()
    batch_size = 8
    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start : batch_start + batch_size]
        judgement_result = judge.judge_batch(
            claim,
            [item.text for item in batch],
            max_passages=len(batch),
        )

        for hit in judgement_result.hits:
            item_index = batch_start + hit.index
            if batch_start <= item_index < batch_start + len(batch):
                items[item_index].judgement = hit.judgement
                items[item_index].judgement_confidence = hit.confidence
                items[item_index].retrieval_stage = "llm_judged"

    return items


# ==================================================================
# Helpers
# ==================================================================


def _hit_to_result_item(
    hit: dict[str, Any],
    *,
    retrieval_stage: str = "candidate_recall",
) -> RetrievalResultItem:
    """Convert a Milvus search hit to a RetrievalResultItem."""
    return RetrievalResultItem(
        result_id=str(uuid4()),
        source_scope="workspace",
        evidence_level="full_text",
        paper_id=hit.get("paper_id"),
        chunk_id=hit.get("chunk_id"),
        artifact_id=hit.get("source_artifact_id"),
        section=hit.get("section"),
        text=(hit.get("text") or "").replace("\x00", ""),  # PostgreSQL rejects NUL
        score=hit.get("score", 0.0),
        retrieval_stage=retrieval_stage,
    )


def _is_low_value_section(section: str | None) -> bool:
    """True if a chunk's section is one we drop from Similar Work candidates.

    References / Acknowledgments / Appendix etc. usually cite related work
    rather than describing it — they produce noise that pushes out genuine
    topical matches. Match is case-insensitive and ignores leading/trailing
    whitespace (section labels in real chunks are inconsistently cased).
    """
    if not section:
        return False
    return section.strip().lower() in LOW_VALUE_SECTIONS


def _paper_max_top_k(
    items: list[RetrievalResultItem],
    top_k: int,
) -> list[RetrievalResultItem]:
    """Keep the highest-scoring item per paper, then the top ``top_k`` papers.

    The retrieval stages over-fetch at the *chunk* level, so without this a
    single paper's many chunks can occupy several of the top-k slots and crowd
    out other papers. The Gate (and the UI) measures similarity / counter
    evidence at the *paper* level — unique papers in the top-k — so keeping
    one chunk per paper makes every slot surface a distinct paper.

    Items without a ``paper_id`` cannot be paper-deduplicated; they are kept
    to fill any remaining slots.
    """
    if not items:
        return []
    best: dict[str, RetrievalResultItem] = {}
    paperless: list[RetrievalResultItem] = []
    for item in items:
        pid = item.paper_id
        if pid is None:
            paperless.append(item)
            continue
        if pid not in best or (item.score or 0) > (best[pid].score or 0):
            best[pid] = item
    ranked = sorted(best.values(), key=lambda i: i.score or 0, reverse=True)[:top_k]
    remaining = top_k - len(ranked)
    if remaining > 0 and paperless:
        ranked.extend(paperless[:remaining])
    return ranked


def _hybrid_rerank_top_k(
    candidates: list[dict[str, Any]],
    reranked: list[RetrievalResultItem],
    top_k: int,
    alpha: float = 0.5,
) -> list[RetrievalResultItem]:
    """Rank candidate chunks by a blend of raw vector score and rerank score.

    The cross-encoder is a strong relevance signal, but for paper-level
    *similar work* it can be too narrow — it demotes papers that share the
    topic yet phrase it differently (the demo Gate missed GSAT / DIR this
    way). Blending the raw Milvus score back in keeps those works in
    contention. Scores are min-max normalized per source, then combined as
    ``alpha * raw + (1 - alpha) * rerank``; the top chunk per paper is kept so
    the top-k slots surface distinct papers.
    """
    if not reranked:
        return []
    raw_by_chunk = {h.get("chunk_id"): h.get("score", 0.0) for h in candidates}
    raw_vals = [v for v in raw_by_chunk.values() if v]
    rerank_vals = [i.score or 0.0 for i in reranked if i.score is not None]

    def norm(vals: list[float], value: float) -> float:
        lo, hi = min(vals), max(vals)
        return (value - lo) / (hi - lo) if hi > lo else 0.5

    best: dict[str, tuple[float, RetrievalResultItem]] = {}
    for item in reranked:
        pid = item.paper_id
        if pid is None:
            continue
        raw = raw_by_chunk.get(item.chunk_id)
        raw_norm = norm(raw_vals, raw) if raw is not None and raw_vals else 0.0
        rerank_norm = norm(rerank_vals, item.score or 0.0) if rerank_vals else 0.0
        hybrid = alpha * raw_norm + (1 - alpha) * rerank_norm
        if pid not in best or hybrid > best[pid][0]:
            best[pid] = (hybrid, item)
    ranked = sorted(best.values(), key=lambda pair: pair[0], reverse=True)
    return [item for _, item in ranked[:top_k]]


def _diversify_and_sort_counter_items(
    items: list[RetrievalResultItem],
) -> list[RetrievalResultItem]:
    """Apply role-priority sort + per-paper chunk cap to Counter Evidence items.

    Sort key:
      1. role priority (contradicts < qualifies < supports/overlaps < unknown)
      2. judgement_confidence desc (within same role)
      3. score desc (within same role + confidence; tie-break on reranker score)
      4. paper diversity cap: at most ``COUNTER_EVIDENCE_MAX_CHUNKS_PER_PAPER``
         chunks from any single paper survive.

    The cap is applied AFTER sort so we keep the most-confident role
    representations of each paper, not the first N by score.
    """
    if not items:
        return items

    # Sort by (role priority, -confidence, -score, stable order for ties)
    def sort_key(item: RetrievalResultItem) -> tuple[int, float, float]:
        return (
            COUNTER_ROLE_PRIORITY.get(item.judgement, 99),
            -item.judgement_confidence,
            -item.score,
        )

    ranked = sorted(items, key=sort_key)

    # Apply per-paper cap.
    by_paper_count: dict[str, int] = {}
    capped: list[RetrievalResultItem] = []
    for item in ranked:
        pid = item.paper_id or ""
        if by_paper_count.get(pid, 0) >= COUNTER_EVIDENCE_MAX_CHUNKS_PER_PAPER:
            continue
        by_paper_count[pid] = by_paper_count.get(pid, 0) + 1
        capped.append(item)
    return capped


def _spread_sample_indices(total: int, max_samples: int = 5) -> list[int]:
    """Pick evenly spread indices from [0, total) for representative sampling."""
    if total <= max_samples:
        return list(range(total))
    step = total / max_samples
    return [int(i * step) for i in range(max_samples)]
