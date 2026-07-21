"""Task Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal[
    "queued",
    "running",
    "waiting_for_user",
    "succeeded",
    "failed",
    "cancel_requested",
    "cancelled",
]


class TaskCreate(BaseModel):
    """Internal create schema (not directly exposed via HTTP in Phase 1b)."""

    workspace_id: str | None = None
    task_type: str = Field(..., min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskUpdate(BaseModel):
    """Internal update schema for state transitions (not directly exposed).

    The public PATCH endpoint only allows cancel + resume; status transitions
    from workers go through the service layer directly.
    """

    status: TaskStatus | None = None
    progress: float | None = Field(None, ge=0.0, le=1.0)
    payload_patch: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    celery_task_id: str | None = None


class TaskRead(BaseModel):
    """Full task as returned from the API."""

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


class TaskListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[TaskRead]
    total: int
    limit: int
    offset: int
