"""Artifact Pydantic schemas.

Artifacts are typically created implicitly during Paper upload, so we only
expose Read shapes plus an internal Create used by the service layer.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRead(BaseModel):
    """Artifact as returned from the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    kind: str
    file_path: str
    original_filename: str | None = None
    mime_type: str | None = None
    size_bytes: int
    is_deleted: bool = False
    created_at: datetime
    updated_at: datetime


class ArtifactCreateInternal(BaseModel):
    """Internal schema used by the service layer (not exposed via HTTP)."""

    workspace_id: str
    kind: str = Field(..., pattern=r"^(pdf|parsed_text|chunk_index|report)$")
    file_path: str
    original_filename: str | None = None
    mime_type: str | None = None
    size_bytes: int = 0
