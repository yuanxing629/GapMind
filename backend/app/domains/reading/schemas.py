"""阅读库和论文标注的 schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ReadingPaperRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    reading_item_id: str
    paper_id: str
    workspace_id: str
    workspace_name: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    source: str = "manual"
    external_paper_id: str | None = None
    primary_artifact_id: str | None = None
    parse_status: str = "not_applicable"
    page_count: int = 0
    parsed_text_chars: int = 0
    quality_flags: list[str] = Field(default_factory=list)
    parse_error: str | None = None
    parsed_markdown_artifact_id: str | None = None
    chunk_count: int = 0
    reading_status: str = "unread"
    last_read_page: int = 1
    last_read_at: datetime | None = None
    added_at: datetime
    updated_at: datetime


class ReadingPaperListResponse(BaseModel):
    items: list[ReadingPaperRead]
    total: int
    limit: int
    offset: int


class ReadingProgressUpdate(BaseModel):
    page_number: int = Field(default=1, ge=1)
    status: Literal["unread", "reading", "completed"] | None = None


class PaperAnnotationCreate(BaseModel):
    kind: Literal["note", "highlight", "underline"] = "note"
    page_number: int = Field(default=1, ge=1)
    selected_text: str | None = Field(default=None, max_length=20000)
    note_content: str = Field(..., min_length=1, max_length=20000)
    color: str = Field(default="#fff1a8", max_length=16)
    rects: list[dict[str, Any]] = Field(default_factory=list)
    source_text_hash: str | None = Field(default=None, max_length=128)


class PaperAnnotationUpdate(BaseModel):
    kind: Literal["note", "highlight", "underline"] | None = None
    page_number: int | None = Field(default=None, ge=1)
    selected_text: str | None = Field(default=None, max_length=20000)
    note_content: str | None = Field(default=None, min_length=1, max_length=20000)
    color: str | None = Field(default=None, max_length=16)
    rects: list[dict[str, Any]] | None = None
    source_text_hash: str | None = Field(default=None, max_length=128)


class PaperAnnotationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    paper_id: str
    workspace_id: str
    artifact_id: str | None = None
    kind: str
    page_number: int
    selected_text: str | None = None
    note_content: str
    color: str
    rects: list[dict[str, Any]] = Field(default_factory=list)
    source_text_hash: str | None = None
    created_at: datetime
    updated_at: datetime
