"""Knowledge 的 Pydantic schemas（Phase 1b 只读）。"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

KnowledgeType = Literal[
    "paper",
    "method",
    "task",
    "dataset",
    "claim",
    "evidence",
    "limitation",
]

KnowledgeStatus = Literal[
    "raw_source",
    "extracted_candidate",
    "evidence_backed_proposal",
    "human_confirmed",
    "experiment_validated",
    "deprecated",
    "rejected",
    "invalidated",
]

CreatedBy = Literal["user", "agent", "system"]


class KnowledgeItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    paper_id: str | None = None
    canonical_entity_id: str | None = None
    extraction_run_id: str | None = None
    item_key: str | None = None
    type: KnowledgeType
    canonical_name: str
    content: dict[str, Any] = Field(default_factory=dict)
    source_provenance: dict[str, Any] = Field(default_factory=dict)
    created_by: CreatedBy = "system"
    confidence: float
    status: KnowledgeStatus
    version: int
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime


class KnowledgeItemListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[KnowledgeItemRead]
    total: int
    limit: int
    offset: int


class KnowledgeRelationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    source_id: str
    target_id: str
    relation_type: str
    confidence: float
    payload: dict[str, Any] = Field(default_factory=dict)
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime


class KnowledgeRelationListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[KnowledgeRelationRead]
    total: int
    limit: int
    offset: int


class KnowledgeGraphNodeRead(BaseModel):
    """投影为 graph 节点的知识条目。"""

    id: str
    label: str
    type: str
    workspace_id: str
    paper_id: str | None = None
    canonical_entity_id: str | None = None
    confidence: float
    status: str
    content: dict[str, Any] = Field(default_factory=dict)
    node_kind: str = "knowledge"
    paper_title: str | None = None
    entity_type: str | None = None
    mention_text: str | None = None
    knowledge_item_id: str | None = None
    display_label: str | None = None
    display_type: str | None = None
    importance_score: float = 0.0
    relation_count: int = 0
    evidence_count: int = 0
    paper_count: int = 0
    mention_count: int = 0
    knowledge_item_count: int = 0
    confirmed_item_count: int = 0
    aliases: list[str] = Field(default_factory=list)
    supporting_paper_ids: list[str] = Field(default_factory=list)
    supporting_paper_ids_truncated: bool = False
    review_status: str | None = None


class KnowledgeGraphEdgeRead(BaseModel):
    """投影为 graph 边的关系。"""

    id: str
    source: str
    target: str
    relation_type: str
    confidence: float
    payload: dict[str, Any] = Field(default_factory=dict)
    display_label: str | None = None
    source_label: str | None = None
    target_label: str | None = None
    relation_group: str | None = None
    occurrence_count: int = 0
    paper_count: int = 0
    evidence_count: int = 0
    supporting_paper_ids: list[str] = Field(default_factory=list)
    supporting_item_ids: list[str] = Field(default_factory=list)


class KnowledgeGraphResponse(BaseModel):
    """面向 Knowledge UI 的工作区级 graph 投影。"""

    workspace_id: str
    nodes: list[KnowledgeGraphNodeRead] = Field(default_factory=list)
    edges: list[KnowledgeGraphEdgeRead] = Field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0
    truncated: bool = False
    limit: int = 0
    offset: int = 0
    projection_mode: str = "all"
    loaded_nodes: int = 0
    loaded_edges: int = 0
    has_more: bool = False
    node_counts: dict[str, int] = Field(default_factory=dict)
    relation_counts: dict[str, int] = Field(default_factory=dict)
    workspace_counts: dict[str, int] = Field(default_factory=dict)
    truncation_reason: str | None = None
    seed_node_id: str | None = None
    depth: int = 0


class KnowledgeGraphSearchResult(BaseModel):
    node_id: str
    label: str
    node_kind: str
    type: str
    paper_title: str | None = None
    confidence: float = 0.0
    paper_count: int = 0
    mention_count: int = 0
    knowledge_item_count: int = 0
    evidence_count: int = 0


class KnowledgeGraphSearchResponse(BaseModel):
    items: list[KnowledgeGraphSearchResult] = Field(default_factory=list)


GraphRAGNodeKind = Literal[
    "paper",
    "canonical_entity",
    "knowledge_item",
    "evidence_span",
    "chunk",
]
GraphRAGReviewStatus = Literal["confirmed", "candidate", "rejected"]


class GraphRAGSeedRead(BaseModel):
    """由 dense 命中适配得到的有界 graph seed。"""

    node_id: str
    node_kind: GraphRAGNodeKind
    workspace_id: str
    paper_id: str | None = None
    chunk_id: str | None = None
    rank: int = Field(default=0, ge=0)
    score: float = 0.0


class GraphRAGNodeRead(BaseModel):
    """当前请求范围内、带有明确 provenance 身份的 graph 节点。"""

    id: str
    kind: GraphRAGNodeKind
    workspace_id: str
    label: str
    paper_id: str | None = None
    item_id: str | None = None
    canonical_entity_id: str | None = None
    chunk_id: str | None = None
    evidence_span_id: str | None = None
    type: str | None = None
    status: str | None = None
    review_status: GraphRAGReviewStatus = "candidate"


class GraphRAGEvidenceRead(BaseModel):
    """为 graph 路径从 PostgreSQL 重新检索得到的 Evidence。"""

    evidence_span_id: str
    workspace_id: str
    paper_id: str
    item_id: str
    artifact_id: str | None = None
    chunk_id: str | None = None
    section: str | None = None
    excerpt: str = ""
    start_char: int | None = None
    end_char: int | None = None
    relation: str = "supports"
    confidence: float = 0.0
    review_status: GraphRAGReviewStatus = "candidate"
    # 仅用于诊断的分数，用来保持 graph evidence 与当前问题关联；
    # 它不是 scientific confidence，也不是确认状态。
    query_relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)


class GraphRAGEdgeRead(BaseModel):
    """经过校验的 graph 边；source 和 target 必须存在于路径节点中。"""

    id: str
    type: str
    source: str
    target: str
    workspace_id: str
    paper_id: str | None = None
    supporting_item_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    review_status: GraphRAGReviewStatus = "candidate"


class GraphRAGPathRead(BaseModel):
    """有界且可审计的路径；它不是持久化的 scientific fact。"""

    path_id: str
    workspace_id: str
    nodes: list[GraphRAGNodeRead] = Field(default_factory=list)
    edges: list[GraphRAGEdgeRead] = Field(default_factory=list)
    supporting_paper_ids: list[str] = Field(default_factory=list)
    supporting_item_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    evidence: list[GraphRAGEvidenceRead] = Field(default_factory=list)
    review_status: GraphRAGReviewStatus = "candidate"


class EvidenceSpanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    knowledge_item_id: str
    paper_id: str
    artifact_id: str | None = None
    artifact_kind: str | None = None
    artifact_version: str | None = None
    chunk_index: int | None = None
    start_char: int | None = None
    end_char: int | None = None
    text: str | None = None
    relation: str = "supports"
    confidence: float
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime


class EvidenceSpanListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[EvidenceSpanRead]
    total: int


class EvidenceContextRead(BaseModel):
    """parsed-markdown 源文本及 UI 需要高亮的范围。"""

    workspace_id: str
    paper_id: str
    artifact_id: str
    artifact_kind: str
    filename: str | None = None
    content: str
    spans: list[EvidenceSpanRead] = Field(default_factory=list)


class ExtractionRejectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    extraction_run_id: str
    paper_id: str
    batch_index: int | None = None
    rejection_kind: str
    stage: str
    reason_code: str
    reason_detail: str
    item_type: str | None = None
    canonical_name: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_preview: str | None = None
    created_at: datetime


class ExtractionRejectionListResponse(BaseModel):
    items: list[ExtractionRejectionRead]
    total: int
    limit: int
    offset: int


class ExtractionRejectionCreate(BaseModel):
    workspace_id: str
    extraction_run_id: str
    paper_id: str
    batch_index: int | None = None
    rejection_kind: Literal["item", "relation", "output"]
    stage: Literal[
        "schema_validation",
        "evidence_resolution",
        "relation_resolution",
        "dedup_exact",
        "dedup_semantic",
    ]
    reason_code: str = Field(min_length=1, max_length=64)
    reason_detail: str = Field(min_length=1)
    item_type: str | None = None
    canonical_name: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_preview: str | None = None


# ----------------------------------------------------------------- 创建 schemas（Phase 3）
class KnowledgeItemCreate(BaseModel):
    """创建知识条目的请求体（agent 抽取或用户输入）。"""

    workspace_id: str
    paper_id: str | None = None
    canonical_entity_id: str | None = None
    extraction_run_id: str | None = None
    item_key: str | None = None
    type: KnowledgeType
    canonical_name: str
    content: dict[str, Any] = Field(default_factory=dict)
    source_provenance: dict[str, Any] = Field(default_factory=dict)
    created_by: CreatedBy = "agent"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    status: KnowledgeStatus = "extracted_candidate"


class KnowledgeItemReview(BaseModel):
    """单个 Knowledge Item 的 Human-in-the-loop 审核操作。"""

    action: Literal["confirm", "edit", "reject"]
    canonical_name: str | None = Field(default=None, min_length=1, max_length=512)
    content: dict[str, Any] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str | None = Field(default=None, max_length=4000)


class PaperMentionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    paper_id: str
    canonical_entity_id: str
    knowledge_item_id: str | None = None
    mention_text: str
    artifact_id: str | None = None
    start_char: int | None = None
    end_char: int | None = None
    confidence: float
    status: str
    created_at: datetime
    updated_at: datetime


class KnowledgeRelationCreate(BaseModel):
    workspace_id: str
    source_id: str
    target_id: str
    relation_type: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)


class EvidenceSpanCreate(BaseModel):
    workspace_id: str
    knowledge_item_id: str
    paper_id: str
    artifact_id: str | None = None
    artifact_kind: str | None = None
    artifact_version: str | None = None
    start_char: int | None = None
    end_char: int | None = None
    text: str | None = None
    relation: str = "supports"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


# ---------------------------------------------------- 严格抽取输出
class EvidencePointer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_char: int = Field(ge=0)
# LLM 偏移仅作提示。允许为零，并在持久化前根据精确 evidence_text 修复。
    end_char: int = Field(ge=0)


class MethodContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    problem_addressed: str = Field(min_length=1)
    inputs: list[str]
    outputs: list[str]
    key_idea: str = Field(min_length=1)
    training_paradigm: Literal["post-hoc", "intrinsic", "hybrid"] | None = None
    computational_cost: Literal["low", "moderate", "high"] | None = None
    code_repository: str | None = None


class TaskContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    problem_type: Literal[
        "classification",
        "regression",
        "ranking",
        "generation",
        "optimization",
        "other",
    ]
    input_data: str = Field(min_length=1)
    evaluation_protocol: str | None = None
    common_datasets: list[str]


class DatasetContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    domain: Literal[
        "chemistry",
        "biology",
        "social-network",
        "citation-network",
        "vision",
        "nlp",
        "other",
    ]
    size: int | None = Field(default=None, ge=0)
    modality: Literal["graph", "text", "image", "tabular", "multimodal"] | None = None
    source_url: str | None = None
    license: str | None = None


class ClaimContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1)
    claim_type: Literal["positive", "negative", "comparative", "conditional"]
    scope: str | None = None
    conditions: str | None = None


class LimitationContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    limitation_type: Literal[
        "computational",
        "expressiveness",
        "scalability",
        "faithfulness",
        "stability",
        "data-dependency",
        "other",
    ]
    severity: Literal["low", "moderate", "high"] | None = None
    affected_scenarios: list[str]
    proposed_fixes: list[str]


class ExtractionItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_name: str = Field(min_length=1)
    source_provenance: EvidencePointer
    evidence_text: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class MethodExtractionItem(ExtractionItemBase):
    type: Literal["method"]
    content: MethodContent


class TaskExtractionItem(ExtractionItemBase):
    type: Literal["task"]
    content: TaskContent


class DatasetExtractionItem(ExtractionItemBase):
    type: Literal["dataset"]
    content: DatasetContent


class ClaimExtractionItem(ExtractionItemBase):
    type: Literal["claim"]
    content: ClaimContent


class LimitationExtractionItem(ExtractionItemBase):
    type: Literal["limitation"]
    content: LimitationContent


ExtractionItem = Annotated[
    MethodExtractionItem
    | TaskExtractionItem
    | DatasetExtractionItem
    | ClaimExtractionItem
    | LimitationExtractionItem,
    Field(discriminator="type"),
]


class ExtractionRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: KnowledgeType
    source_name: str = Field(min_length=1)
# 关系名称在校验后规范化。未知值只拒绝该关系，不影响其他有效抽取条目。
    relation: str = Field(min_length=1, max_length=64)
    target_type: KnowledgeType
    target_name: str = Field(min_length=1)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExtractionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExtractionItem]
    relations: list[ExtractionRelation] = Field(default_factory=list)
