"""Artifact 的 Pydantic schemas。

Artifact 通常在 Paper 上传期间隐式创建，因此这里只暴露 Read 结构，
以及 service 层使用的内部 Create 结构。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ArtifactRead(BaseModel):
    """API 返回的 Artifact。"""

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
    """service 层使用的内部 schema（不通过 HTTP 暴露）。"""

    workspace_id: str
    kind: str = Field(..., pattern=r"^(pdf|parsed_text|parsed_markdown|chunk_index|paper_image|report)$")
    file_path: str
    original_filename: str | None = None
    mime_type: str | None = None
    size_bytes: int = 0
