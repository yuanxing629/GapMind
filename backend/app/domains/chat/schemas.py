"""HTTP contracts for the Chat domain."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    workspace_id: str | None = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("title cannot be empty")
        return value


class ChatConversationUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title cannot be empty")
        return value


class ChatImageInput(BaseModel):
    """A browser image encoded as a data URL for the current request."""

    filename: str = Field(default="image", min_length=1, max_length=512)
    mime_type: str = Field(..., min_length=1, max_length=128)
    data_url: str = Field(..., min_length=1)


class ChatMessageCreate(BaseModel):
    content: str = Field(..., min_length=1)
    workspace_id: str | None = None
    research_plan_id: str | None = None
    source_artifact_ids: list[str] = Field(default_factory=list, max_length=4)
    images: list[ChatImageInput] = Field(default_factory=list, max_length=3)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content cannot be empty")
        return value

    @field_validator("source_artifact_ids")
    @classmethod
    def normalize_source_artifact_ids(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item and item.strip()))


class ChatConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    workspace_id: str | None = None
    model: str | None = None
    last_message_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ChatMessageEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    workspace_id: str
    paper_id: str | None = None
    artifact_id: str | None = None
    chunk_id: str | None = None
    paper_title: str | None = None
    section: str | None = None
    excerpt: str
    start_char: int | None = None
    end_char: int | None = None
    score: float
    rank: int
    created_at: datetime
    updated_at: datetime


class ChatMessageImageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: datetime
    updated_at: datetime


class CitationCheckRead(BaseModel):
    """Result of validating [En] markers in an assistant message against its citations."""
    referenced: list[int] = Field(default_factory=list)
    broken: list[int] = Field(default_factory=list)
    ok: bool = True
    grounded_without_citations: bool = False


class SourceCheckRead(BaseModel):
    """Validation of [P1]/[D1]/[C1] markers against the source passport."""

    referenced: list[str] = Field(default_factory=list)
    broken: list[str] = Field(default_factory=list)
    ok: bool = True


class CitationQualityRead(BaseModel):
    """Persisted audit of the bounded citation/source quality gate."""

    status: Literal["not_needed", "passed", "repaired", "rejected"] = "not_needed"
    attempts: int = Field(default=0, ge=0, le=1)
    initial_broken_citations: list[int] = Field(default_factory=list)
    initial_grounded_without_citations: bool = False
    initial_broken_sources: list[str] = Field(default_factory=list)
    final_broken_citations: list[int] = Field(default_factory=list)
    final_grounded_without_citations: bool = False
    final_broken_sources: list[str] = Field(default_factory=list)
    fallback: bool = False


class RetrievalAuditRead(BaseModel):
    """Persisted, non-sensitive retrieval observability for one answer."""

    request_id: str = ""
    status: str = "unknown"
    diagnostic_code: str | None = None
    recall_count: int | None = Field(default=None, ge=0)
    returned_chunk_count: int = Field(default=0, ge=0)
    final_paper_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    reranker_status: Literal[
        "applied", "enabled_no_rerank", "degraded", "disabled", "unknown"
    ] = "unknown"


class ChatMessageSourceRead(BaseModel):
    """One explicitly labelled context source used for an answer."""

    marker: str
    source_type: Literal["plan", "paper", "report", "code_draft"]
    source_id: str
    label: str
    title: str
    status: str
    detail: str | None = None


class ChatContextPlanOption(BaseModel):
    id: str
    title: str
    research_question: str
    status: str


class ChatContextArtifactOption(BaseModel):
    id: str
    plan_id: str
    source_type: Literal["report", "code_draft"]
    label: str
    title: str
    status: str


class ChatContextOptionsResponse(BaseModel):
    plans: list[ChatContextPlanOption] = Field(default_factory=list)
    artifacts: list[ChatContextArtifactOption] = Field(default_factory=list)


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: str
    content: str
    status: str
    error_message: str | None = None
    sequence: int
    model: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    prompt_chars: int | None = Field(default=None, ge=0)
    response_chars: int | None = Field(default=None, ge=0)
    first_token_latency_ms: float | None = Field(default=None, ge=0)
    completion_latency_ms: float | None = Field(default=None, ge=0)
    grounding_status: str = "not_requested"
    retrieval_diagnostic_code: str | None = None
    citation_quality: CitationQualityRead = Field(default_factory=CitationQualityRead)
    retrieval_audit: RetrievalAuditRead = Field(default_factory=RetrievalAuditRead)
    citations: list[ChatMessageEvidenceRead] = Field(default_factory=list)
    images: list[ChatMessageImageRead] = Field(default_factory=list)
    citation_check: CitationCheckRead | None = None
    sources: list[ChatMessageSourceRead] = Field(default_factory=list)
    source_check: SourceCheckRead | None = None
    created_at: datetime
    updated_at: datetime


class ChatConversationListResponse(BaseModel):
    items: list[ChatConversationRead]
    total: int
    limit: int
    offset: int


class ChatConversationDetail(BaseModel):
    conversation: ChatConversationRead
    messages: list[ChatMessageRead]


class ChatSendResponse(BaseModel):
    conversation: ChatConversationRead
    user_message: ChatMessageRead
    assistant_message: ChatMessageRead


class ChatDeleteResponse(BaseModel):
    id: str
    deleted: bool


class ChatEvidenceContextRead(BaseModel):
    evidence: ChatMessageEvidenceRead
    available: bool
    artifact_kind: str | None = None
    filename: str | None = None
    content: str | None = None
    message: str | None = None
