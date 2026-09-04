"""检索 service 层：分块索引与语义搜索。

步骤 ④：读取 chunks JSONL（Contract B）→ 使用 BGE-M3 向量化 → 写入 Milvus。
步骤 ⑤：semantic_search / find_similar_work / find_counter_evidence（Phase 3）。

流水线阶段（Contract D retrieval_stage）：
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

# Similar Work 聚合：Top-K 中每篇论文最多保留这么多分块。
# 流程会从 Milvus 过量召回，因此这是对“聚合后”候选池的限制，而不是原始 Milvus
# 召回的限制——召回质量得以保留。
SIMILAR_WORK_MAX_CHUNKS_PER_PAPER = 2

# Counter Evidence 聚合：使用更严格的上限。每个 claim 的反证通常集中在少数论文中；
# 限制上限可以避免一篇论文的多个 contradicting 分块挤掉另一篇论文中少见但真正
# qualifying 的命中。
COUNTER_EVIDENCE_MAX_CHUNKS_PER_PAPER = 3

# Counter Evidence 结果的排序角色优先级。数字越小，优先级越高。
# 反证检索的价值在于 contradicts / qualifies；保留 overlaps 和 supports，
# 只是为了明确说明没有更强反证。
COUNTER_ROLE_PRIORITY: dict[str, int] = {
    "contradicts": 0,
    "qualifies": 1,
    "supports": 2,
    "overlaps": 2,
    "unknown": 3,
}

# 这些章节通常很少能回答“这篇论文是否属于相似工作”。它们一般只是引用相关工作，
# 而不是描述相关工作；保留在候选池中会让少数高被引论文仅凭参考文献密度占据 Top-K。
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

# 有意保持这个映射较小并面向用户。Provider/Milvus 异常字符串会记录到运维日志，
# 但不会作为 API 诊断返回，因为它们可能暴露拓扑或凭据相关细节。
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
    """将检索阶段失败映射为稳定且不敏感的代码。"""

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
# 步骤 ④：将论文分块索引到 Milvus
# ==================================================================


def index_paper_chunks(
    workspace_id: str,
    paper_id: str,
    *,
    db: Session,
    force_reindex: bool = False,
) -> IndexChunksResult:
    """主入口：加载 chunk_index Artifact → 向量化 → 写入 Milvus。

    幂等执行：除非 force_reindex=True，否则跳过已索引分块。
    """
    start_time = time.perf_counter()
    gateway = get_embedding_gateway()

    result = IndexChunksResult(
        workspace_id=workspace_id,
        paper_id=paper_id,
        embedding_model=gateway.model,
        embedding_dim=gateway.dim,
    )

# 1. 从 JSONL 加载分块
    chunks = _load_chunks_jsonl(db, workspace_id, paper_id)
    if not chunks:
        result.error = f"No chunks found for paper {paper_id}"
        logger.warning("index.no_chunks", workspace_id=workspace_id, paper_id=paper_id)
        return result

    result.total_chunks = len(chunks)

# 2. 幂等性：跳过已经索引的分块
    if force_reindex:
        milvus_client.delete_by_paper(paper_id, workspace_id=workspace_id)
        to_index = chunks
    else:
        existing_ids = milvus_client.get_existing_chunk_ids(
            paper_id,
            workspace_id=workspace_id,
        )
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

# 3. 使用 BGE-M3 将文本向量化
    texts = [c.text for c in to_index]
    logger.info(
        "index.embedding_start",
        paper_id=paper_id,
        chunk_count=len(texts),
    )
    embedding_result = gateway.embed_texts(texts)

# 4. 构建 Milvus 记录
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

# 5. 写入 Milvus（分批处理，避免单次请求过大）
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
    """在论文所属工作区内解析其规范 chunk_index Artifact。"""
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

# 兼容 Paper 指针填充之前创建的历史记录。只在同一工作区内搜索不可变的
# chunk-index 类型；绝不回退到未限定范围的文件系统路径。
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
    """从论文的存储 Artifact 读取并校验分块（契约 B）。"""
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
    """将检索到的 Milvus 命中解析为存储中的不可变偏移。"""
    return next(
        (
            chunk
            for chunk in _load_chunks_jsonl(db, workspace_id, paper_id)
            if chunk.chunk_id == chunk_id
        ),
        None,
    )


# ==================================================================
# 步骤 ⑤：检索函数（输出契约 D）
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
    """在 workspace 内执行通用语义搜索。

    流程：向量召回 →（可选）重排序 →（可选）论文多样化 → 返回。
    ``exclude_paper_ids`` 会下推到 Milvus 过滤器（见 ``milvus_client.search``），
    并通过 ``filters_applied`` 返回。
    ``diversify_by_paper`` 面向回答生成：重排序后每篇论文只保留最强分块，
    避免一篇长论文占用全部证据槽位。它默认关闭，以保持公开语义搜索 API 的分块级行为。
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
# 阶段 1：向量召回（为 reranker 过量召回）
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

# 阶段 2：重排序
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
    """查找与给定论文相似的其他论文分块。

    流程：多向量召回 → 排除同论文 →（可选）rerank → 返回。
    使用该论文自身的 chunk 作为查询（多向量召回）。
    """
    start_time = time.perf_counter()
    request_id = str(uuid4())
    stage = "embedding"

    try:
        gateway = get_embedding_gateway()
        stage = "data"
# 加载目标论文中的代表性分块作为查询
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

# 最多使用 5 个代表性分块（均匀覆盖论文内容）
        sample_indices = _spread_sample_indices(len(chunks), max_samples=5)
        query_texts = [chunks[i].text for i in sample_indices]

# 将所有查询分块向量化
        stage = "embedding"
        embed_result = gateway.embed_texts(query_texts)
        stage = "milvus"

# 排除集合：源论文始终排除（按定义它不能是“相似工作”），另外加入调用方提供的论文。
        excluded = set(exclude_paper_ids or set()) | {paper_id}

# 分别搜索并收集命中。将排除条件下推到 Milvus 过滤器，避免被排除论文进入召回池。
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
# 防御性剔除：Milvus 过滤器本应已经排除 ``excluded``，但即使过滤器存在缺陷或
# 只实现了部分逻辑，也不能让源论文泄漏到“相似工作”结果中。
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

# 步骤 1：剔除低价值章节（References / Acknowledgments 等）。
# 这些章节很少包含真正的“相似工作”，通常只是引用它们。
        filtered_hits = [h for h in all_hits if not _is_low_value_section(h.get("section"))]
        if not filtered_hits:
# 所有候选都是低价值章节时，回退到现有候选。
            filtered_hits = all_hits

# 步骤 2：论文级聚合与单论文分块上限。
# 按论文分组，每篇取 MAX_CHUNKS_PER_PAPER 个最高分分块，再按分数重新合并为候选池。
        by_paper: dict[str, list[dict[str, Any]]] = {}
        for hit in filtered_hits:
            pid = hit.get("paper_id") or ""
            by_paper.setdefault(pid, []).append(hit)
        candidates: list[dict[str, Any]] = []
        for pid, hits in by_paper.items():
            hits.sort(key=lambda h: h.get("score", 0.0), reverse=True)
            candidates.extend(hits[:SIMILAR_WORK_MAX_CHUNKS_PER_PAPER])

# 步骤 3：对多样化候选池重排序（未启用 reranker 时按分数排序）。
        diagnostic_codes: list[str] = []
        if use_reranker and len(candidates) > 1:
            rerank_query = query_texts[0][:500]
# 对整个候选池重排序，再与原始向量分数融合，并为每篇论文只保留最高分分块：
# 这样 Top-K 槽位可以呈现 k 篇不同论文，而仅凭 reranker 会被降权的论文
#（主题相同但措辞不同）仍有机会保留。
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
    """查找可能反驳或限定给定 claim 的分块。

    流程：向量召回 → rerank → LLM/NLI judge → 返回。
    Contract D 要求 counter_evidence 必须经过 rerank 或 LLM/NLI 判断；使用 judge 时，
    retrieval_stage = 'llm_judged'。
    """
    start_time = time.perf_counter()
    request_id = str(uuid4())
    stage = "embedding"

    try:
        gateway = get_embedding_gateway()
# 阶段 1：向量召回（过量召回）。将 claim 源论文的排除条件下推到 Milvus 过滤器，
# 使源论文自身的分块不会进入召回池——否则它们会挤掉真正的反证。
        recall_k = top_k * 3 if (use_reranker or use_judge) else top_k
        query_vector = gateway.embed_one(claim_text)
        stage = "milvus"
        hits = milvus_client.search(
            query_vector,
            workspace_id,
            top_k=recall_k,
            exclude_paper_ids=exclude_paper_ids,
        )
# 双重保障：Milvus 过滤器负责主要排除，防御性剔除用于防止特定 Milvus 版本中
# 过滤语法回归导致的泄漏。
        if exclude_paper_ids:
            hits = [hit for hit in hits if hit.get("paper_id") not in exclude_paper_ids]

        if not hits:
            latency = (time.perf_counter() - start_time) * 1000
            return RetrievalResponse(
                request_id=request_id,
                workspace_id=workspace_id,
                query=claim_text,
                purpose="counter_evidence",
# 完全没有 Milvus 候选。重要的是：这不是系统失败，只是没有找到足够相似的内容。
                status="succeeded",
                items=[],
                total=0,
                latency_ms=round(latency, 2),
                filters_applied={"excluded_paper_ids": sorted(exclude_paper_ids or set())},
                empty_reason="retrieval_empty",
            )

# 阶段 2：对整个召回池重排序，再为每篇论文保留最高分分块，使 Top-K 槽位呈现 k 篇
# 不同论文。一篇论文的大量分块否则会挤掉其他论文的反证，而 Gate 按论文级别衡量召回。
        diagnostic_codes: list[str] = []
        if use_reranker and len(hits) > 1:
            reranked_items = _paper_max_top_k(
                _rerank_hits(claim_text, hits, len(hits), diagnostic_codes), top_k
            )
        else:
            reranked_items = _paper_max_top_k(
                [_hit_to_result_item(hit) for hit in hits], top_k
            )

# 阶段 3：LLM 判断（NLI 分类）
        if use_judge and reranked_items:
            items = _judge_items(claim_text, reranked_items)
        else:
            items = reranked_items

# 阶段 4：论文多样化与角色优先级排序。每个 claim 的反证通常集中在少数论文中；
# 限制每篇论文的贡献，避免一篇论文的多个 contradicting 分块挤掉另一篇论文中
# 单个 qualifying 命中。
        items = _diversify_and_sort_counter_items(items)

# 确定状态：Judge 失败哨兵（任意零置信度 unknown）会将响应置为 "degraded"，
# 让 UI 能区分它与干净的“没有找到反证”。
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

# 确定 empty_reason。UI 必须区分以下三个互斥状态（见 ``CounterEmptyReason``）：
#   1. retrieval_empty：Milvus 返回 0 个候选
#   2. judge_failed：Judge 无法对任何候选分类（零置信度 unknown 哨兵）
#   3. genuinely_no_counter_evidence：Judge 已运行，但只返回 supports /
#      overlaps / 非零置信度 unknown，没有真正的反证存在于工作区中。
# 第四种情况（items 包含 contradicts/qualifies）不设置 empty_reason，Top-K 可直接使用。
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
# 内部流程阶段
# ==================================================================


def _rerank_hits(
    query: str,
    hits: list[dict[str, Any]],
    top_k: int,
    diagnostic_codes: list[str] | None = None,
) -> list[RetrievalResultItem]:
    """使用 cross-encoder 对 Milvus 命中重排序并返回 top_k 条目。"""
    reranker = get_reranker_gateway()
    documents = [hit.get("text", "") for hit in hits]

    try:
        rerank_result = reranker.rerank(query, documents, top_n=top_k)
    except Exception as e:
# 优雅降级：回退到按向量分数排序
        logger.warning(
            "retrieval.rerank_failed_fallback",
            error=str(e),
            diagnostic_code="reranker_degraded",
        )
        if diagnostic_codes is not None:
            diagnostic_codes.append("reranker_degraded")
        hits_sorted = sorted(hits, key=lambda h: h.get("score", 0.0), reverse=True)
        return [_hit_to_result_item(hit) for hit in hits_sorted[:top_k]]

# 将重排序后的索引映射回命中结果
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
    """对重排序后的条目应用 LLM 判断（仅用于 counter_evidence）。"""
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
# 辅助函数
# ==================================================================


def _hit_to_result_item(
    hit: dict[str, Any],
    *,
    retrieval_stage: str = "candidate_recall",
) -> RetrievalResultItem:
    """将 Milvus 搜索命中转换为 RetrievalResultItem。"""
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
    """判断分块章节是否属于会从 Similar Work 候选中剔除的章节。

    References / Acknowledgments / Appendix 等章节通常只引用相关工作，而不是描述相关
    方法，会产生噪声并挤出真正的主题匹配。匹配不区分大小写，并忽略首尾空白（真实
    chunk 中的章节标签大小写并不一致）。
    """
    if not section:
        return False
    return section.strip().lower() in LOW_VALUE_SECTIONS


def _paper_max_top_k(
    items: list[RetrievalResultItem],
    top_k: int,
) -> list[RetrievalResultItem]:
    """每篇论文保留最高分条目，再保留前 ``top_k`` 篇论文。

    检索阶段在 *chunk* 层过召回；没有这一步时，同一论文的多个 chunk 会占据多个 top-k
    槽位，挤出其他论文。Gate（以及 UI）在 *paper* 层衡量相似工作 / counter evidence，
    即 top-k 中的唯一论文数，因此每篇论文保留一个 chunk 可以让每个槽位展示不同论文。

    没有 ``paper_id`` 的条目无法按论文去重，会用于填充剩余槽位。
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
    """按原始向量分数与重排序分数的融合值对候选分块排序。

    cross-encoder 是很强的相关性信号，但对于 paper-level 的 *similar work* 可能过窄——
    它会降低与查询共享 topic、但表述不同的论文（demo Gate 曾因此漏掉 GSAT / DIR）。
    将原始 Milvus score 融回后，可以让这些论文继续参与竞争。分数按来源做 min-max
    归一化，再按 ``alpha * raw + (1 - alpha) * rerank`` 合并；每篇论文只保留最高分
    chunk，使 top-k 槽位展示不同论文。
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
    """对 Counter Evidence 条目应用角色优先级排序和单论文分块上限。

    排序键：
      1. role 优先级（contradicts < qualifies < supports/overlaps < unknown）
      2. judgement_confidence 降序（同一 role 内）
      3. score 降序（同一 role 和 confidence 内，最后按 reranker score 打破平局）
      4. 论文多样性上限：每篇论文最多保留 ``COUNTER_EVIDENCE_MAX_CHUNKS_PER_PAPER`` 个 chunk。

    上限在排序后应用，因此保留的是每篇论文 confidence 最高的 role 表示，而不是按
    score 取前 N 条。
    """
    if not items:
        return items

# 按（角色优先级、-confidence、-score、平局时的稳定顺序）排序
    def sort_key(item: RetrievalResultItem) -> tuple[int, float, float]:
        return (
            COUNTER_ROLE_PRIORITY.get(item.judgement, 99),
            -item.judgement_confidence,
            -item.score,
        )

    ranked = sorted(items, key=sort_key)

# 应用单论文上限。
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
    """从 [0, total) 中均匀选择索引，用于代表性采样。"""
    if total <= max_samples:
        return list(range(total))
    step = total / max_samples
    return [int(i * step) for i in range(max_samples)]
