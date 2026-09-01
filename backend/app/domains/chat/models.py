"""Persistent models for global and workspace-grounded AI chat."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class ChatConversation(Base, UUIDPKMixin, TimestampMixin):
    """A soft-deletable conversation scoped to its acting owner."""

    __tablename__ = "chat_conversations"
    __table_args__ = (
        Index("ix_chat_conversations_last_message_at", "last_message_at"),
        Index("ix_chat_conversations_owner_id", "owner_id"),
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False, default="新对话")
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, default="user")
    workspace_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(default=False, nullable=False, index=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChatMessage(Base, UUIDPKMixin, TimestampMixin):
    """One user or assistant message in a conversation."""

    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_chat_message_sequence"),
        Index("ix_chat_messages_conversation_id", "conversation_id"),
    )

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="completed")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Generation-only observability. Nullable for historical rows, failed
    # calls, and non-streaming first-token latency which is not observable.
    prompt_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    grounding_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_requested"
    )
    # Immutable provenance snapshot for this answer. Paper rows remain in
    # ``chat_message_evidence`` for source navigation; this field also records
    # plan/report/code provenance without presenting those artifacts as papers.
    source_manifest: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    # Mechanical citation/source quality gate audit. This is deliberately a
    # small JSON snapshot rather than a queryable document: it is read with
    # the message and is not used for retrieval or filtering.
    citation_quality: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Retrieval-only observability snapshot. It contains counts/status/timing,
    # not raw query text or provider error details.
    retrieval_audit: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # Stable, non-sensitive retrieval diagnosis.  Raw provider/Milvus errors
    # stay in server logs and are never persisted into the workspace UI.
    retrieval_diagnostic_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    citations: Mapped[list["ChatMessageEvidence"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="ChatMessageEvidence.rank",
    )
    images: Mapped[list["ChatMessageImage"]] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        order_by="ChatMessageImage.created_at",
    )


class ChatMessageImage(Base, UUIDPKMixin, TimestampMixin):
    """A user-uploaded image attached to one chat message.

    Images are chat materials only. They are not Artifacts, are not indexed in
    the workspace corpus, and are served through the chat ownership boundary.
    """

    __tablename__ = "chat_message_images"
    __table_args__ = (
        Index("ix_chat_message_images_message_id", "message_id"),
    )

    message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    message: Mapped[ChatMessage] = relationship(back_populates="images")


class ChatMessageEvidence(Base, UUIDPKMixin, TimestampMixin):
    """A persisted retrieval hit cited by one assistant message."""

    __tablename__ = "chat_message_evidence"
    __table_args__ = (
        Index("ix_chat_message_evidence_message_id", "message_id"),
        Index("ix_chat_message_evidence_workspace_id", "workspace_id"),
    )

    message_id: Mapped[str] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[str | None] = mapped_column(
        ForeignKey("papers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    chunk_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    paper_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    section: Mapped[str | None] = mapped_column(String(512), nullable=True)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    start_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_char: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)

    message: Mapped[ChatMessage] = relationship(back_populates="citations")
