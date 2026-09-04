"""workspace agents 的 HTTP 契约。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


AgentType = Literal[
    "research_plan",
    "code_generation",
    "analyze",
    "write",
    "respond",
    "deep_research",
]


class AgentRunCreate(BaseModel):
    agent_type: AgentType
    prompt: str = Field(..., min_length=1, max_length=12000)
    conversation_id: str
    input: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt")
    @classmethod
    def clean_prompt(cls, value: str) -> str:
        return value.strip()


class AgentStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str
    sequence: int
    stage: str
    status: str
    summary: str
    details: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class AgentArtifactRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    run_id: str
    artifact_type: str
    filename: str
    mime_type: str
    content: str
    metadata: dict[str, Any] = Field(
        validation_alias="metadata_payload", serialization_alias="metadata"
    )
    validation_status: str
    created_at: datetime
    updated_at: datetime


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    workspace_id: str
    conversation_id: str | None = None
    trigger_message_id: str | None = None
    assistant_message_id: str | None = None
    task_id: str | None = None
    parent_run_id: str | None = None
    agent_type: str
    status: str
    current_stage: str
    progress: float
    input_payload: dict[str, Any]
    context_snapshot: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None
    requires_confirmation: bool
    created_at: datetime
    updated_at: datetime


class AgentRunDetail(AgentRunRead):
    steps: list[AgentStepRead] = Field(default_factory=list)
    artifacts: list[AgentArtifactRead] = Field(default_factory=list)


class AgentRunListResponse(BaseModel):
    items: list[AgentRunRead]
    total: int
    limit: int
    offset: int


class AgentConfirmResponse(BaseModel):
    run: AgentRunDetail
    research_plan_id: str | None = None
