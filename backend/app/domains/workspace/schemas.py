"""Workspace 的 Pydantic schemas。

分离 Create / Update / Read 结构，使 API surface 明确：
- Create：name 必填，profile 字段可选
- Update：所有字段可选（PATCH 语义）
- Read：完整 workspace，由 GET/POST/PATCH 返回
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkspaceBase(BaseModel):
    """Create 与 Update 共用的字段，除 name 外均可选。"""

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
    """POST /api/v1/workspaces 的请求体。"""

    name: str = Field(..., min_length=1, max_length=255)


class WorkspaceUpdate(WorkspaceBase):
    """PATCH /api/v1/workspaces/{id} 的请求体。

    所有字段均可选。设为 None 的字段会被忽略（不会置空）；若要清除字段，请对文本
    使用显式空字符串，对列表使用空列表。
    """


class WorkspaceRead(BaseModel):
    """API 返回的完整 workspace。"""

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
    """分页列表响应。"""

    model_config = ConfigDict(from_attributes=True)

    items: list[WorkspaceRead]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# W0 研究就绪度（GET /workspaces/{id}/readiness）
# ---------------------------------------------------------------------------


class ReadinessBlockingAction(BaseModel):
    """一个可解释的阻塞步骤：做什么、为什么以及在哪里做。"""

    action: str
    reason: str
    href: str


class ReadinessDimension(BaseModel):
    """一个就绪度维度（corpus / retrieval / knowledge / discover / research）。

    ``ready`` 表示可用；``waiting`` 表示后台流水线 task
    仍在运行（不是用户操作）；否则该维度会被标记为阻塞，并由 ``blocking_actions``
    说明应做什么以及在哪里做。
    """

    key: str
    label: str
    ready: bool
    waiting: bool
    summary: str
    blocking_actions: list[ReadinessBlockingAction] = Field(default_factory=list)


class WorkspaceReadinessCounts(BaseModel):
    """概览进度条和统计信息使用的单一来源计数。"""

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
    """用户下一步应该执行的唯一操作。"""

    title: str
    description: str
    href: str
    label: str


class WorkspaceReadiness(BaseModel):
    """GET /workspaces/{id}/readiness 返回的完整就绪度文档。"""

    workspace_id: str
    counts: WorkspaceReadinessCounts
    dimensions: list[ReadinessDimension]
    recommended_next_action: ReadinessRecommendedAction
