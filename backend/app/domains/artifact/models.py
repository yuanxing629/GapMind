"""Artifact ORM models.

An Artifact is an immutable file: a PDF upload, a parsed-text dump, a
generated report, etc. Artifacts are workspace-scoped and referenced by
Papers (and later by Tasks, KnowledgeItems, TimelineEvents).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Artifact(Base, UUIDPKMixin, TimestampMixin):
    """A file owned by a workspace.

    `kind` distinguishes artifact roles:
      - "pdf"          : original PDF upload
      - "parsed_text"  : extracted plain text (Phase 2)
      - "chunk_index"  : chunked text + offsets (Phase 2)
      - "report"       : generated report (Phase 5+)
    """

    __tablename__ = "artifacts"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
