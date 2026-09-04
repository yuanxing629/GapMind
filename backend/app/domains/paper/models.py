"""Paper ORM 模型。

Paper 是与 Artifact（PDF 上传文件）关联的研究论文元数据记录。Phase 1b 支持手动录入
元数据；Phase 2 增加 PDF 解析状态跟踪（parse_status、parsed_at、chunk_count）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class Paper(Base, UUIDPKMixin, TimestampMixin):
    """工作区中的研究论文。

    `primary_artifact_id` 指向原始 PDF Artifact。派生 artifact（parsed_text、chunk_index）
    由 Phase 2 的 parse_pdf worker task 创建，并保存在 artifacts 表中。
    """

    __tablename__ = "papers"

    workspace_id: Mapped[str] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    primary_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )

# 书目信息
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

# 来源信息
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    external_paper_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

# PDF 解析状态（Phase 2）
# parse_status：pending | parsing | parsed | failed | not_applicable
#   - pending：已有 PDF，等待 parse_pdf 任务启动
#   - parsing：parse_pdf 任务运行中
#   - parsed：解析完成，已有分块
#   - failed：解析失败（关联 Task 的 error 字段包含详细信息）
#   - not_applicable：未附加 PDF（仅有元数据的论文）
    parse_status: Mapped[str] = mapped_column(
        String(32), default="not_applicable", nullable=False, index=True
    )
    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parsed_text_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
# 指向 parse_pdf 生成的 parsed_text artifact
    parsed_text_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
# 指向 chunk_index artifact（包含分块列表的 JSON 文件）
    chunk_index_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )
# 指向 parsed_markdown artifact（包含标题和结构的 .md 文件）
    parsed_markdown_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True, index=True
    )

# 知识抽取状态（Phase 3）
# pending | extracting | extracted | failed | not_applicable
    extract_status: Mapped[str] = mapped_column(
        String(32), default="not_applicable", nullable=False, index=True
    )
    extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

# 生命周期
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
