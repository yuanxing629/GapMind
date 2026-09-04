"""Artifact ORM 模型。

Artifact 是不可变文件：PDF 上传文件、解析文本转储、生成报告等。
Artifact 具有工作区范围，由 Paper（以及后续的 Task、KnowledgeItem、TimelineEvent）引用。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Artifact(Base, UUIDPKMixin, TimestampMixin):
    """workspace 所有的文件。

    `kind` 用于区分 Artifact 角色：
      - "pdf"          ：原始 PDF 上传文件
      - "parsed_text"  ：抽取的纯文本
      - "parsed_markdown"：感知布局的解析 Markdown
      - "chunk_index"  ：分块文本 + 偏移量
      - "paper_image" ：从论文 PDF 抽取的图片资源
      - "report"       ：生成的报告
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
