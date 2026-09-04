"""Task 的 Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TaskStatus = Literal[
    "queued",
    "running",
    "waiting_for_user",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
]


def summarize_task_error(value: str | None) -> str | None:
    """返回可安全展示在 API 和 UI 中的可操作消息。"""
    if not value:
        return None
    lowered = value.lower()
    if "validation errors for extractionoutput" in lowered:
        return "Knowledge extraction returned an invalid structure. Retry the extraction or inspect worker logs."
    if "all extracted items were rejected" in lowered:
        return "Knowledge extraction could not verify any evidence in the paper text."
    if "evidence_text" in lowered and "parsed_markdown" in lowered:
        return "Some extracted evidence could not be located in the paper text."
    if "api_key" in lowered:
        return "The configured model service is unavailable."
    first_line = value.splitlines()[0].strip()
    if len(first_line) <= 180:
        return first_line
    return "Task failed. Inspect backend or worker logs for technical details."


class TaskCreate(BaseModel):
    """内部创建 schema（Phase 1b 不直接通过 HTTP 暴露）。"""

    workspace_id: str | None = None
    task_type: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    """用于状态转换的内部更新 schema（不直接暴露）。

    public PATCH endpoint 只允许 cancel + resume；worker 的状态转换直接经过 service 层。
    """

    status: TaskStatus | None = None
    progress: float | None = Field(None, ge=0.0, le=1.0)
    payload_patch: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    celery_task_id: str | None = None


class TaskRead(BaseModel):
    """API 返回的完整任务。"""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str | None = None
    task_type: str
    status: TaskStatus
    progress: float
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    celery_task_id: str | None = None
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime

    @field_validator("error", mode="before")
    @classmethod
    def _sanitize_error(cls, value: object) -> str | None:
        return summarize_task_error(str(value)) if value is not None else None


class TaskListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[TaskRead]
    total: int
    limit: int
    offset: int
