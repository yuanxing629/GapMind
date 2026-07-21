"""Paper ORM models.

A Paper is a research paper metadata record associated with an Artifact
(the PDF upload). Phase 1b supports manual metadata entry; Phase 2 adds
PDF parsing state tracking (parse_status, parsed_at, chunk_count).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Paper(Base, UUIDPKMixin, TimestampMixin):
    """A research paper in a workspace.

    `primary_artifact_id` points to the original PDF Artifact. Derived
    artifacts (parsed_text, chunk_index) are created by the parse_pdf
    worker task in Phase 2 and live in the artifacts table.
    """

    __tablename__ = "papers"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    primary_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Bibliographic metadata
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Provenance
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    external_paper_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # PDF parsing state (Phase 2)
    # parse_status: pending | parsing | parsed | failed | not_applicable
    #   - pending: has PDF, waiting for parse_pdf task to start
    #   - parsing: parse_pdf task running
    #   - parsed: parsing completed, chunks available
    #   - failed: parsing failed (error field on related Task has details)
    #   - not_applicable: no PDF attached (metadata-only paper)
    parse_status: Mapped[str] = mapped_column(
        String(32), default="not_applicable", nullable=False, index=True
    )
    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Points to the parsed_text artifact produced by parse_pdf
    parsed_text_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Points to the chunk_index artifact (JSON file with chunk list)
    chunk_index_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Lifecycle
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
