"""Retrieval domain 的 Pydantic schemas。

定义 chunk 索引（Contract B 输入）和检索响应（Contract D 输出）的数据结构。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Judge 针对 claim 为检索分块评分时分配的角色。顺序为：先列出 contradictions
#（对用户信息量最高），再列出 qualifications（仍有信息量），然后是 overlap /
# support（对“这是否是反例？”的信号较弱），最后是 unknown（Judge 失败或没有信号）。
CounterRole = Literal["contradicts", "qualifies", "supports", "overlaps", "unknown"]

# ``counter_evidence`` 的空结果原因代码。用户必须能区分“没有找到任何内容”
# 与“无法判断找到的内容”，因为两者会触发不同的 Discover Run 后续操作。
CounterEmptyReason = Literal[
    "retrieval_empty",            # Milvus returned 0 candidates
    "judge_failed",               # Judge LLM call failed for every candidate
    "genuinely_no_counter_evidence",  # Judge ran, no contradicting / qualifying chunk
]

# 可安全展示给 UI 的稳定失败诊断信息。embedding provider 或 Milvus 的异常文本
# 有意不纳入此契约，因为其中可能包含基础设施细节或凭据。
RetrievalDiagnosticCode = Literal[
    "embedding_unavailable",
    "milvus_unavailable",
    "collection_unloaded",
    "reranker_degraded",
    "unknown",
]


# ------------------------------------------------------------------
# 契约 B：分块记录（来自 parse_pdf JSONL 的输入）
# ------------------------------------------------------------------


class ChunkRecord(BaseModel):
    """论文规范 chunk_index Artifact 中的一条记录。

    按 Contract B（data_contracts_v1.md §3）进行校验。
    """

    schema_version: str = "1.0.0"
    chunk_id: str
    workspace_id: str
    paper_id: str
    source_artifact_id: str
    source_artifact_kind: str = "parsed_text"
    chunk_index: int
    section: str | None = None
    subsection: str | None = None
    text: str
    start_char: int
    end_char: int
    page_start: int = 0
    page_end: int = 0
    tokens_estimate: int = 0
    chunk_version: str = "v1"
    created_at: str = ""


# ------------------------------------------------------------------
# 索引结果
# ------------------------------------------------------------------


class IndexChunksResult(BaseModel):
    """将一篇论文的分块索引到 Milvus 后的结果。"""

    workspace_id: str
    paper_id: str
    total_chunks: int = 0
    indexed_count: int = 0
    skipped_count: int = 0
    embedding_model: str = ""
    embedding_dim: int = 0
    duration_ms: float = 0.0
    error: str | None = None


# ------------------------------------------------------------------
# 契约 D：检索响应（输出到 Discover Agent / UI）
# ------------------------------------------------------------------


class RetrievalResultItem(BaseModel):
    """单条检索命中（契约 D 条目）。"""

    result_id: str = ""
    source_scope: str = "workspace"  # workspace | external
    evidence_level: str = "full_text"  # full_text | metadata_only
    paper_id: str | None = None
    external_paper_id: str | None = None
    paper_title: str | None = None
    paper_year: int | None = None
    chunk_id: str | None = None
    artifact_id: str | None = None
    section: str | None = None
    text: str = ""
    score: float = 0.0
    retrieval_stage: str = "candidate_recall"
# 限制为 Judge 的词汇表，使下游代码（UI、Discover Agent）可以按值分支，
# 而不是通过字符串模式匹配。
    judgement: CounterRole = "unknown"
    judgement_confidence: float = 0.0


class RetrievalResponse(BaseModel):
    """完整检索响应（契约 D）。"""

    schema_version: str = "1.0.0"
    request_id: str = ""
    workspace_id: str
    query: str = ""
    purpose: str = "semantic"  # semantic | similar_work | counter_evidence
    status: str = "succeeded"  # succeeded | degraded | failed
    items: list[RetrievalResultItem] = Field(default_factory=list)
    total: int = 0
    latency_ms: float = 0.0
    filters_applied: dict = Field(default_factory=dict)
    error: str | None = None
    diagnostic_code: RetrievalDiagnosticCode | None = None
# 仅当 ``total == 0`` 且 ``purpose == "counter_evidence"`` 时填充，
# 使 Discover Agent / UI 可以区分三种空状态：
    # retrieval_empty | judge_failed | genuinely_no_counter_evidence.
    empty_reason: CounterEmptyReason | None = None
