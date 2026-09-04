"""全局和 workspace-grounded AI chat 的持久化模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class ChatConversation(Base, UUIDPKMixin, TimestampMixin):
    """按实际操作者隔离、可软删除的会话。"""

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
    """会话中的一条用户或 assistant 消息。"""

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
    # 仅用于生成过程的可观测性。历史行、失败调用以及无法观测首 token 延迟的
    # 非流式调用都允许为空。
    prompt_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    completion_latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    grounding_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_requested"
    )
    # 本次回答的不可变 provenance 快照。Paper 行仍保留在 ``chat_message_evidence``
    # 中用于 source navigation；此字段还记录 plan/report/code provenance，
    # 但不会将这些 artifacts 展示为论文。
    source_manifest: Mapped[list[dict]] = mapped_column(JSON, nullable=False, default=list)
    # 机械化 citation/source quality gate 审计。这是一个小型 JSON 快照，而不是可查询
    # 文档：它与消息一起读取，不用于 retrieval 或 filtering。
    citation_quality: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # 仅用于 retrieval 的可观测性快照。它包含 count/status/timing，不包含原始 query
    # 文本或 provider 错误详情。
    retrieval_audit: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # 稳定且非敏感的 retrieval 诊断。原始 provider/Milvus 错误只保留在服务端日志中，
    # 永远不会持久化到 workspace UI。
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
    """附加到一条 chat 消息的用户上传图片。

    图片仅属于 Chat 材料。它们不是 Artifact，不会被索引到工作区语料中，
    并通过 Chat 所有权边界提供服务。
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
    """一条 assistant 消息引用的持久化检索命中。"""

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
