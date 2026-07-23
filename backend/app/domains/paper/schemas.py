"""Paper Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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


class SemanticScholarAuthor(BaseModel):
    """The author fields used by the search result UI."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    author_id: str | None = Field(None, alias="authorId")
    name: str | None = None


class SemanticScholarPaper(BaseModel):
    """A deliberately small, forward-compatible S2 paper projection."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    paper_id: str = Field(alias="paperId")
    corpus_id: int | None = Field(None, alias="corpusId")
    external_ids: dict[str, Any] | None = Field(None, alias="externalIds")
    url: str | None = None
    title: str | None = None
    abstract: str | None = None
    year: int | None = None
    publication_date: str | None = Field(None, alias="publicationDate")
    authors: list[SemanticScholarAuthor] = Field(default_factory=list)
    venue: str | None = None
    citation_count: int | None = Field(None, alias="citationCount")
    reference_count: int | None = Field(None, alias="referenceCount")
    influential_citation_count: int | None = Field(
        None, alias="influentialCitationCount"
    )
    is_open_access: bool | None = Field(None, alias="isOpenAccess")
    open_access_pdf: dict[str, Any] | None = Field(None, alias="openAccessPdf")
    fields_of_study: list[str] | None = Field(None, alias="fieldsOfStudy")
    s2_fields_of_study: list[dict[str, Any]] | None = Field(
        None, alias="s2FieldsOfStudy"
    )
    publication_types: list[str] | None = Field(None, alias="publicationTypes")
    tldr: dict[str, Any] | None = None


class SemanticScholarSearchResponse(BaseModel):
    """Normalized wrapper for both offset and token based S2 searches."""

    total: int = 0
    offset: int = 0
    next: int | None = None
    token: str | None = None
    data: list[SemanticScholarPaper] = Field(default_factory=list)


class SemanticScholarImportRequest(BaseModel):
    """Import one search result into a selected Workspace as metadata."""

    semantic_scholar_paper_id: str = Field(..., min_length=1, max_length=255)
