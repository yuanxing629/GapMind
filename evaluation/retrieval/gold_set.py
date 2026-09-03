"""Gold-set schemas for the Retrieval Gate benchmark.

A GoldSet is a frozen, human-annotated manifest describing what "good"
retrieval looks like for a given case. It feeds ``run_eval.py`` which
executes the three retrieval functions and computes Stage-2 Gate metrics.

Paper references stay *portable*: a ``paper_ref`` is a string resolved to
a local UUID at run time (title match → external_paper_id match → direct
UUID). This keeps gold files human-readable (annotators reference papers
by title) while the manifest never hard-codes local DB primary keys.

Location rationale: ``docs/phase3_smoke_validation_and_next_plan.md`` §6 V2
says "使用 ``evaluation/retrieval/`` 统一运行并保存结果" — this package
lives at that canonical path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Freeze(BaseModel):
    """Version lock recorded in every report for reproducibility.

    Fields map to ``app.core.config.Settings``. If any value changes, a
    report produced before the change is not comparable with one produced
    after it.
    """

    chunk_version: str = "v1"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    # A concrete model is recorded by each frozen manifest. Keep new manifests
    # provider-neutral instead of baking in one deployment's model name.
    judge_model: str = ""


class SemanticSearchQuery(BaseModel):
    """Free-text query whose gold answer is *the target paper*.

    Gate metric: is ``target_paper_ref`` present in the top-K results?
    """

    query_id: str
    query: str = Field(min_length=3, max_length=500)
    target_paper_ref: str
    note: str | None = None


class SimilarWorkQuery(BaseModel):
    """Given a source paper, which *other* papers are paper-level relevant?

    Gate metric: how many ``relevant_paper_refs`` appear in the top-K
    (deduplicated to one hit per paper)? The source paper itself must be
    excluded by the retrieval call.
    """

    query_id: str
    source_paper_ref: str
    relevant_paper_refs: list[str] = Field(min_length=1, max_length=20)
    note: str | None = None


class CounterRole(BaseModel):
    """Expected role of a counter-evidence hit for a claim."""

    paper_ref: str
    role: Literal["contradicts", "qualifies", "supports", "overlaps", "unknown"]


class CounterEvidenceQuery(BaseModel):
    """A claim whose gold answer is a set of (paper, role) pairs.

    Coarse gate metric: paper-level Recall@10 over ``gold_roles`` (role
    ignored). Role-correct recall is reported separately as a diagnostic —
    the Stage-2 threshold is on paper recall, not role accuracy.

    ``claim_type`` groups claims for the V4 special validation
    (``A_fact`` | ``B_qualified`` | ``C_first_novel``).
    """

    query_id: str
    claim_text: str = Field(min_length=3, max_length=2000)
    source_paper_ref: str
    gold_roles: list[CounterRole] = Field(min_length=1, max_length=20)
    claim_type: str | None = Field(default=None)
    note: str | None = None


class GoldSet(BaseModel):
    """A complete annotated benchmark for one case."""

    schema_version: str = "1.0.0"
    case_id: str
    corpus_version: str
    freeze: Freeze = Field(default_factory=Freeze)
    workspace_hint: str | None = Field(
        default=None,
        description="Local UUID of the workspace. CLI --workspace-id overrides it.",
    )
    semantic_search: list[SemanticSearchQuery] = Field(default_factory=list)
    similar_work: list[SimilarWorkQuery] = Field(default_factory=list)
    counter_evidence: list[CounterEvidenceQuery] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_at_least_one_benchmark(self) -> "GoldSet":
        if not (self.semantic_search or self.similar_work or self.counter_evidence):
            raise ValueError("GoldSet must declare at least one benchmark")
        return self


__all__ = [
    "Freeze",
    "GoldSet",
    "SemanticSearchQuery",
    "SimilarWorkQuery",
    "CounterEvidenceQuery",
    "CounterRole",
]
