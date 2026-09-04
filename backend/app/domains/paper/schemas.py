"""Paper 的 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PaperBase(BaseModel):
    """共用的元数据字段。"""

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
    """POST /api/v1/workspaces/{id}/papers 的请求体（JSON 仅元数据创建）。

    PDF 上传请改用 `/papers/upload` endpoint。通过 JSON 创建时必须提供 `title`，不能在
    没有标题的情况下创建仅含元数据的论文。上传时 router 会在内部构建 PaperCreate，
    并可能保留 title=None，以便 service 从 PDF 元数据中补全标题。
    """

    title: str | None = Field(None, min_length=1, max_length=512)


class PaperUpdate(PaperBase):
    """PATCH /api/v1/workspaces/{id}/papers/{paper_id} 的请求体。"""


class PaperRead(BaseModel):
    """API 返回的完整论文。"""

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
# Phase 2：解析状态
    parse_status: str = "not_applicable"
    parsed_at: datetime | None = None
    page_count: int = 0
    parsed_text_chars: int = 0
    quality_flags: list[str] = Field(default_factory=list)
    parse_error: str | None = None
    chunk_count: int = 0
    parsed_text_artifact_id: str | None = None
    chunk_index_artifact_id: str | None = None
    parsed_markdown_artifact_id: str | None = None
    extract_status: str = "not_applicable"
    extracted_at: datetime | None = None
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime


class PaperListResponse(BaseModel):
    """分页列表响应。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[PaperRead]
    total: int
    limit: int
    offset: int


class SemanticScholarAuthor(BaseModel):
    """搜索结果 UI 使用的作者字段。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    author_id: str | None = Field(None, alias="authorId")
    name: str | None = None


class SemanticScholarPaper(BaseModel):
    """有意保持精简且向前兼容的 S2 论文投影。"""

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
    """同时包装 offset 和 token 两种 S2 搜索结果的规范化结构。"""

    total: int = 0
    offset: int = 0
    next: int | None = None
    token: str | None = None
    data: list[SemanticScholarPaper] = Field(default_factory=list)


class SemanticScholarSearchHistoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    query: str
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: str
    result_count: int = 0
    created_at: datetime


class SemanticScholarFavoriteCreate(BaseModel):
    paper: SemanticScholarPaper
    note: str | None = Field(default=None, max_length=2000)


class SemanticScholarFavoriteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    semantic_scholar_paper_id: str
    paper: SemanticScholarPaper
    note: str | None = None
    created_at: datetime
    updated_at: datetime


class SemanticScholarImportRequest(BaseModel):
    """将一条搜索结果作为元数据导入选定的 Workspace。"""

    semantic_scholar_paper_id: str = Field(..., min_length=1, max_length=255)
    download_open_access_pdf: bool = True
