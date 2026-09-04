"""Gap discovery 的 Schema 3.0 契约与 API 载荷。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

EntityType = Literal[
    "RESEARCH_PROBLEM",
    "TASK",
    "METHOD",
    "MODEL",
    "DOMAIN",
    "OTHER_SCIENTIFIC_TERM",
]
RelationType = Literal[
    "ADDRESSES",
    "USES",
    "APPLIED_TO",
    "EXTENDS",
    "HAS_LIMITATION",
    "PART_OF",
    "RELATED_TO",
]
ProblemType = Literal["prior_work_gap", "residual_limitation"]


class GapPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_name: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    research_domain: list[str] = Field(default_factory=list)


class GapEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    name_original: str = Field(min_length=1)
    name_normalized_zh: str = Field(min_length=1)
    type: EntityType
    description_zh: str = ""


class GapRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(pattern=r"^R[1-9][0-9]*$")
    source_entity_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    relation_type: RelationType
    target_entity_id: str = Field(pattern=r"^E[1-9][0-9]*$")


class GapMethod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method_id: str = Field(pattern=r"^M[1-9][0-9]*$")
    corresponding_entity_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    method_strategy_zh: str = Field(min_length=1, max_length=40)
    mechanism_zh: str = ""

    @field_validator("method_strategy_zh")
    @classmethod
    def short_label(cls, value: str) -> str:
        if any(char in value for char in "。！？；\n"):
            raise ValueError("method_strategy_zh must be a short label")
        return value


class GapProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_id: str = Field(pattern=r"^P[1-9][0-9]*$")
    corresponding_entity_id: str = Field(pattern=r"^E[1-9][0-9]*$")
    problem_label_zh: str = Field(min_length=1, max_length=40)
    problem_type: ProblemType
    description_zh: str = Field(min_length=1)

    @field_validator("problem_label_zh")
    @classmethod
    def short_label(cls, value: str) -> str:
        if any(char in value for char in "。！？；\n"):
            raise ValueError("problem_label_zh must be a short label")
        return value


class GapAnnotationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["3.0"]
    paper: GapPaper
    entities: list[GapEntity] = Field(min_length=1, max_length=15)
    relations: list[GapRelation] = Field(default_factory=list, max_length=15)
    methods: list[GapMethod] = Field(min_length=1, max_length=2)
    problems: list[GapProblem] = Field(min_length=1, max_length=3)


class GapExtractionRequest(BaseModel):
    paper_ids: list[str] = Field(min_length=1, max_length=200)
    force: bool = False


class GapExtractionTask(BaseModel):
    paper_id: str
    task_id: str
    status: str
    skipped: bool = False
    input_mode: str | None = None
    knowledge_extraction_run_id: str | None = None
    dependency_status: str = "not_checked"


class GapExtractionResponse(BaseModel):
    tasks: list[GapExtractionTask]


class GapAnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str
    workspace_id: str
    paper_id: str
    artifact_id: str
    task_id: str | None = None
    input_sha256: str
    knowledge_extraction_run_id: str | None = None
    knowledge_context_sha256: str | None = None
    input_mode: str = "core_markdown_legacy_v1"
    source_knowledge_item_ids: list[str] = Field(default_factory=list)
    source_evidence_span_ids: list[str] = Field(default_factory=list)
    context_char_count: int = 0
    context_fallback_reason: str | None = None
    schema_version: str
    prompt_version: str
    model_provider: str
    model_name: str
    model_digest: str | None = None
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    status: str
    attempts: int
    output: dict[str, Any] | None = None
    validation_errors: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None
    stale: bool = False
    created_at: datetime
    updated_at: datetime


class GapAnnotationListResponse(BaseModel):
    items: list[GapAnnotationRead]
    total: int


class GapBoardRebuildRequest(BaseModel):
    paper_ids: list[str] = Field(default_factory=list, max_length=500)


class GapBoardAxis(BaseModel):
    concept_id: str
    label: str
    aliases: list[str] = Field(default_factory=list)
    paper_count: int = 0
    paper_ids: list[str] = Field(default_factory=list)


class GapBoardCell(BaseModel):
    method_concept_id: str
    problem_concept_id: str
    addressed: bool
    addressed_paper_ids: list[str] = Field(default_factory=list)
    limitation_paper_ids: list[str] = Field(default_factory=list)
    cooccurrence_paper_ids: list[str] = Field(default_factory=list)
    explicit_limitation: bool = False
    candidate_score: float = 0.0
    candidate_tier: str = "corpus_only"
    candidate_reasons: list[str] = Field(default_factory=list)
    eligible_for_discovery: bool = False
    verification_status: str = "unverified"


class GapBoardRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    version: int
    filters: dict[str, Any] = Field(default_factory=dict)
    method_axes: list[GapBoardAxis] = Field(default_factory=list)
    problem_axes: list[GapBoardAxis] = Field(default_factory=list)
    cells: list[GapBoardCell] = Field(default_factory=list)
    source_annotation_ids: list[str] = Field(default_factory=list)
    candidate_count: int = 0
    created_at: datetime


class GapCandidateDiscoverRequest(BaseModel):
    method_concept_id: str
    problem_concept_id: str
    constraints: str | None = Field(default=None, max_length=4000)
    max_opportunities: int = Field(default=3, ge=1, le=5)
    exploratory: bool = False


class GapCandidateDiscoverResponse(BaseModel):
    run_id: str
    task_id: str | None = None
    status: str
