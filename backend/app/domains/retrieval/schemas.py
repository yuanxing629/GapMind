"""Retrieval domain Pydantic schemas.

Defines the data structures for chunk indexing (Contract B input)
and retrieval responses (Contract D output).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Role the Judge assigns to a retrieved chunk when scoring it against a
# claim. Ordered: contradictions first (most informative for the user),
# qualifications next (still informative), then overlap / support (low signal
# for "is this a counter-example?"), then unknown (judge failure or no signal).
CounterRole = Literal["contradicts", "qualifies", "supports", "overlaps", "unknown"]

# Empty-result reason codes for ``counter_evidence``. The user must be able
# to distinguish "we couldn't find anything" from "we couldn't judge what we
# found" — those lead to different Discover Run follow-ups.
CounterEmptyReason = Literal[
    "retrieval_empty",            # Milvus returned 0 candidates
    "judge_failed",               # Judge LLM call failed for every candidate
    "genuinely_no_counter_evidence",  # Judge ran, no contradicting / qualifying chunk
]

# Safe, stable diagnostics for failures that can be shown by the UI.  The
# exception text from an embedding provider or Milvus is deliberately not part
# of this contract: it may contain infrastructure details or credentials.
RetrievalDiagnosticCode = Literal[
    "embedding_unavailable",
    "milvus_unavailable",
    "collection_unloaded",
    "reranker_degraded",
    "unknown",
]


# ------------------------------------------------------------------
# Contract B: Chunk record (input from parse_pdf JSONL)
# ------------------------------------------------------------------


class ChunkRecord(BaseModel):
    """A single record from a paper's canonical chunk_index Artifact.

    Validates against Contract B (data_contracts_v1.md §3).
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
# Indexing result
# ------------------------------------------------------------------


class IndexChunksResult(BaseModel):
    """Result of indexing one paper's chunks into Milvus."""

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
# Contract D: Retrieval response (output to Discover Agent / UI)
# ------------------------------------------------------------------


class RetrievalResultItem(BaseModel):
    """A single retrieval hit (Contract D item)."""

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
    # Constrained to the Judge's vocabulary so downstream code (UI, Discover
    # Agent) can switch on the value rather than pattern-match strings.
    judgement: CounterRole = "unknown"
    judgement_confidence: float = 0.0


class RetrievalResponse(BaseModel):
    """Full retrieval response (Contract D)."""

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
    # Populated only when ``total == 0`` and ``purpose == "counter_evidence"``
    # so the Discover Agent / UI can distinguish three empty states:
    # retrieval_empty | judge_failed | genuinely_no_counter_evidence.
    empty_reason: CounterEmptyReason | None = None
