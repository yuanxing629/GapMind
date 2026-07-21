"""Timeline Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TimelineEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    event_type: str
    actor: str = "system"
    subject_type: str | None = None
    subject_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    created_at: datetime


class TimelineListResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    items: list[TimelineEventRead]
    total: int
    limit: int
    offset: int


class TimelineRecordInternal(BaseModel):
    """Internal schema used by other services to record events."""

    workspace_id: str
    event_type: str
    actor: str = "system"
    subject_type: str | None = None
    subject_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
