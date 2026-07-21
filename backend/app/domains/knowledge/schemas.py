"""Knowledge Pydantic schemas (read-only for Phase 1b)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

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
    type: KnowledgeType
    canonical_name: str
    content: dict[str, Any] = Field(default_factory=dict)
    source_provenance: dict[str, Any] = Field(default_factory=dict)
    created_by: CreatedBy = "system"
    confidence: float
    status: KnowledgeStatus
    version: int
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


class EvidenceSpanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    knowledge_item_id: str
    paper_id: str
    artifact_id: str | None = None
    chunk_index: int | None = None
    start_char: int | None = None
    end_char: int | None = None
    text: str | None = None
    relation: str = "supports"
    confidence: float
    created_at: datetime
    updated_at: datetime


class EvidenceSpanListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[EvidenceSpanRead]
    total: int
