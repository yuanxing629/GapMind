"""Workspace Pydantic schemas.

Separate Create / Update / Read shapes so the API surface is explicit:
- Create: required name, optional profile fields
- Update: every field optional (PATCH semantics)
- Read: full workspace, returned from GET/POST/PATCH
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkspaceBase(BaseModel):
    """Shared fields between Create and Update - all optional except name."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    topic: str | None = None
    keywords: list[str] = Field(default_factory=list)
    goals: str | None = None
    constraints: str | None = None
    active_questions: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("name cannot be empty or whitespace")
        return v

    @field_validator("keywords", "active_questions")
    @classmethod
    def _strip_str_list(cls, v: list[str]) -> list[str]:
        return [item.strip() for item in v if isinstance(item, str) and item.strip()]


class WorkspaceCreate(WorkspaceBase):
    """Body for POST /api/v1/workspaces."""

    name: str = Field(..., min_length=1, max_length=255)


class WorkspaceUpdate(WorkspaceBase):
    """Body for PATCH /api/v1/workspaces/{id}.

    All fields optional. Fields set to None are ignored (not nulled-out) -
    use explicit empty string for text fields or empty list for list fields
    if you want to clear them.
    """


class WorkspaceRead(BaseModel):
    """Full workspace as returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    topic: str | None = None
    keywords: list[str] = Field(default_factory=list)
    goals: str | None = None
    constraints: str | None = None
    active_questions: list[str] = Field(default_factory=list)
    is_archived: bool = False
    is_demo: bool = False
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime


class WorkspaceListResponse(BaseModel):
    """Paginated list response."""

    model_config = ConfigDict(from_attributes=True)

    items: list[WorkspaceRead]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# W0 research readiness (GET /workspaces/{id}/readiness)
# ---------------------------------------------------------------------------


class ReadinessBlockingAction(BaseModel):
    """One explainable blocking step: what to do, why, and where."""

    action: str
    reason: str
    href: str


class ReadinessDimension(BaseModel):
    """One readiness dimension (corpus / retrieval / knowledge / discover / research).

    ``ready`` means usable; ``waiting`` means a background pipeline task is
    still running (not a user action); otherwise the dimension is blocked and
    ``blocking_actions`` explains what to do and where.
    """

    key: str
    label: str
    ready: bool
    waiting: bool
    summary: str
    blocking_actions: list[ReadinessBlockingAction] = Field(default_factory=list)


class WorkspaceReadinessCounts(BaseModel):
    """Single-source counts used by the overview progress bar and stats."""

    papers: int = 0
    papers_with_pdf: int = 0
    parsed_papers: int = 0
    extracted_papers: int = 0
    knowledge_items: int = 0
    confirmed_items: int = 0
    pending_knowledge: int = 0
    runs: int = 0
    pending_runs: int = 0
    active_tasks: int = 0
    opportunities: int = 0
    pending_opportunities: int = 0
    confirmed_opportunities: int = 0
    research_plans: int = 0


class ReadinessRecommendedAction(BaseModel):
    """The single next step the user should take."""

    title: str
    description: str
    href: str
    label: str


class WorkspaceReadiness(BaseModel):
    """Full readiness document returned by GET /workspaces/{id}/readiness."""

    workspace_id: str
    counts: WorkspaceReadinessCounts
    dimensions: list[ReadinessDimension]
    recommended_next_action: ReadinessRecommendedAction
