"""Paper Pydantic schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PaperBase(BaseModel):
    """Shared metadata fields."""

    model_config = ConfigDict(from_attributes=True)

    title: str | None = Field(None, min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=1900, le=2100)
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("title cannot be empty or whitespace")
        return v

    @field_validator("authors")
    @classmethod
    def _strip_authors(cls, v: list[str]) -> list[str]:
        return [a.strip() for a in v if isinstance(a, str) and a.strip()]


class PaperCreate(PaperBase):
    """Body for POST /api/v1/workspaces/{id}/papers (JSON metadata-only create).

    For PDF upload, use the `/papers/upload` endpoint instead. For JSON
    creation, `title` is required - you can't create a metadata-only paper
    without a title. For upload, the router builds a PaperCreate internally
    and may leave title=None so the service can fill it from PDF metadata.
    """

    title: str | None = Field(None, min_length=1, max_length=512)


class PaperUpdate(PaperBase):
    """Body for PATCH /api/v1/workspaces/{id}/papers/{paper_id}."""


class PaperRead(BaseModel):
    """Full paper as returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    primary_artifact_id: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    source: str = "manual"
    external_paper_id: str | None = None
    # Phase 2: parsing state
    parse_status: str = "not_applicable"
    parsed_at: datetime | None = None
    chunk_count: int = 0
    parsed_text_artifact_id: str | None = None
    chunk_index_artifact_id: str | None = None
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime


class PaperListResponse(BaseModel):
    """Paginated list response."""

    model_config = ConfigDict(from_attributes=True)

    items: list[PaperRead]
    total: int
    limit: int
    offset: int
