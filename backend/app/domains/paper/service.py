"""Paper service 层。

两条创建路径：
  - create_from_metadata：仅 JSON、不含 PDF（较少使用，但适合 Semantic Scholar 命中结果）
  - create_from_upload：通过 ArtifactService 保存 PDF 字节，然后创建指向新 Artifact 的 Paper 行。

两条路径都会通过 TimelineService 记录 Timeline 事件。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.artifact.pdf_metadata import extract_metadata
from app.domains.artifact.service import ArtifactService
from app.domains.paper.models import Paper
from app.domains.paper.schemas import PaperCreate, PaperUpdate
from app.domains.retrieval import milvus_client
from app.domains.workspace.service import WorkspaceService

logger = get_logger(__name__)


class PaperNotFoundError(Exception):
    def __init__(self, paper_id: str) -> None:
        super().__init__(f"Paper not found: {paper_id}")
        self.paper_id = paper_id


class PaperAlreadyHasPdfError(Exception):
    """尝试为已有 PDF 的论文附加 PDF 时抛出的异常。"""

    def __init__(self, paper_id: str) -> None:
        super().__init__(f"Paper already has a PDF: {paper_id}")
        self.paper_id = paper_id


def _stem_filename(filename: str) -> str:
    """返回不含扩展名的文件名，用作默认论文标题。"""
    import os

    return os.path.splitext(filename)[0] or filename


class PaperService:
    """Paper 的 CRUD 与上传操作。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.artifact_service = ArtifactService(db)
# 延迟导入 TimelineService，避免模块加载时潜在的循环导入
#（timeline -> ... 最终可能反向导入 paper）。
        from app.domains.timeline.service import TimelineService

        self.timeline_service = TimelineService(db)

# ------------------------------------------------------------ 创建
    def create_from_metadata(
        self,
        *,
        workspace_id: str,
        payload: PaperCreate,
        source: str = "manual",
        external_paper_id: str | None = None,
    ) -> Paper:
        """仅使用元数据创建论文（不含 PDF）。"""
        if not payload.title or not payload.title.strip():
            raise ValueError("title is required for metadata-only paper creation")
        self._ensure_workspace_exists(workspace_id)
        paper = Paper(
            id=str(uuid4()),
            workspace_id=workspace_id,
            primary_artifact_id=None,
            title=payload.title,
            authors=list(payload.authors),
            year=payload.year,
            abstract=payload.abstract,
            doi=payload.doi,
            arxiv_id=payload.arxiv_id,
            source=source,
            external_paper_id=external_paper_id,
            is_deleted=False,
        )
        self.db.add(paper)
        self.db.commit()
        self.db.refresh(paper)
        self.timeline_service.record(
            workspace_id=workspace_id,
            event_type="paper.created",
            subject_type="paper",
            subject_id=paper.id,
            payload={"title": paper.title, "source": paper.source},
        )
        logger.info("paper.created", paper_id=paper.id, workspace_id=workspace_id)
        return paper

    def create_from_upload(
        self,
        *,
        workspace_id: str,
        payload: PaperCreate,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
    ) -> Paper:
        """在一个事务中创建论文并保存其 PDF。

        顺序：保存 Artifact -> 创建指向它的论文行 -> 记录时间线。
        如果保存 Artifact 后论文创建失败，Artifact 行会成为孤立记录，但会软删除以保持存储一致
        （Phase 1b 简化方案）。

        元数据自动填充：调用方留空的字段（标量为 None，作者为空列表）会尽力从 PDF 内嵌元数据
        字典中填充（通过 PyMuPDF）。调用方提供的值始终优先。
        """
        self._ensure_workspace_exists(workspace_id)
        paper_id = str(uuid4())
        artifact = self.artifact_service.save_upload(
            workspace_id=workspace_id,
            filename=filename,
            content=content,
            mime_type=mime_type,
            kind="pdf",
            paper_id=paper_id,
        )

# 尽力提取元数据，不覆盖用户已经提供的字段。
        pdf_meta = extract_metadata(content)
        title = payload.title or pdf_meta.title or _stem_filename(filename)
        authors = list(payload.authors) if payload.authors else list(pdf_meta.authors)
        year = payload.year if payload.year is not None else pdf_meta.year

        try:
            paper = Paper(
                id=paper_id,
                workspace_id=workspace_id,
                primary_artifact_id=artifact.id,
                title=title,
                authors=authors,
                year=year,
                abstract=payload.abstract,
                doi=payload.doi,
                arxiv_id=payload.arxiv_id,
                source="manual",
                external_paper_id=None,
                parse_status="pending",  # has PDF, waiting for parse_pdf task
                is_deleted=False,
            )
            self.db.add(paper)
            self.db.commit()
            self.db.refresh(paper)
        except Exception:
            self.db.rollback()
# 将 artifact 标记为软删除，避免它出现在列表中。
            artifact.is_deleted = True
            self.db.commit()
            raise

        auto_filled: list[str] = []
        if not payload.title and pdf_meta.title:
            auto_filled.append("title")
        if not payload.authors and pdf_meta.authors:
            auto_filled.append("authors")
        if payload.year is None and pdf_meta.year is not None:
            auto_filled.append("year")

        self.timeline_service.record(
            workspace_id=workspace_id,
            event_type="paper.uploaded",
            subject_type="paper",
            subject_id=paper.id,
            payload={
                "title": paper.title,
                "filename": filename,
                "size_bytes": artifact.size_bytes,
                "artifact_id": artifact.id,
                "auto_filled": auto_filled,
                "page_count": pdf_meta.page_count,
            },
        )
        logger.info(
            "paper.uploaded",
            paper_id=paper.id,
            workspace_id=workspace_id,
            artifact_id=artifact.id,
            size_bytes=artifact.size_bytes,
            auto_filled=auto_filled,
        )

# 启动异步 parse_pdf 任务。采用尽力执行策略：如果派发失败（例如 Redis
# 不可用），论文保持为 "pending"，用户之后可以从 UI 重试。这里只记录日志，
# 不让上传失败。
        try:
            from app.workers.tasks.parse_pdf import spawn_parse_pdf_task

            spawn_parse_pdf_task(self.db, paper.id, workspace_id)
        except Exception as e:
            logger.warning(
                "paper.upload.spawn_parse_failed",
                paper_id=paper.id,
                error=str(e),
            )

        return paper

    def attach_pdf_to_existing(
        self,
        *,
        workspace_id: str,
        paper_id: str,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
    ) -> Paper:
        """为已有的仅元数据 Paper 附加 PDF。

        用于论文通过 `create_from_metadata` 创建后，用户再获得 PDF 的情况。
        设置 `primary_artifact_id`，并尽力使用 PDF 填充仍为空的元数据字段。
        """
        paper = self.get(paper_id)
        if paper.workspace_id != workspace_id:
            raise PaperNotFoundError(paper_id)
        if paper.primary_artifact_id is not None:
            raise PaperAlreadyHasPdfError(paper_id)

        self._ensure_workspace_exists(workspace_id)
        artifact = self.artifact_service.save_upload(
            workspace_id=workspace_id,
            filename=filename,
            content=content,
            mime_type=mime_type,
            kind="pdf",
            paper_id=paper.id,
        )

        pdf_meta = extract_metadata(content)
# 只填充论文记录中仍为空的字段。
        if not paper.title and pdf_meta.title:
            paper.title = pdf_meta.title
        if not paper.authors and pdf_meta.authors:
            paper.authors = list(pdf_meta.authors)
        if paper.year is None and pdf_meta.year is not None:
            paper.year = pdf_meta.year
        paper.primary_artifact_id = artifact.id
# 论文现在已有 PDF，将状态设为 pending，供 parse_pdf 任务处理。
        paper.parse_status = "pending"

        try:
            self.db.commit()
            self.db.refresh(paper)
        except Exception:
            self.db.rollback()
            artifact.is_deleted = True
            self.db.commit()
            raise

        self.timeline_service.record(
            workspace_id=workspace_id,
            event_type="paper.pdf_attached",
            subject_type="paper",
            subject_id=paper.id,
            payload={
                "filename": filename,
                "size_bytes": artifact.size_bytes,
                "artifact_id": artifact.id,
                "page_count": pdf_meta.page_count,
            },
        )
        logger.info(
            "paper.pdf_attached",
            paper_id=paper.id,
            workspace_id=workspace_id,
            artifact_id=artifact.id,
        )

# 启动异步 parse_pdf 任务（与上传路径相同）。
        try:
            from app.workers.tasks.parse_pdf import spawn_parse_pdf_task

            spawn_parse_pdf_task(self.db, paper.id, workspace_id)
        except Exception as e:
            logger.warning(
                "paper.attach_pdf.spawn_parse_failed",
                paper_id=paper.id,
                error=str(e),
            )

        return paper

# ----------------------------------------------------------------- 读取
    def get(self, paper_id: str) -> Paper:
        self._validate_uuid(paper_id)
        p = self.db.get(Paper, paper_id)
        if p is None or p.is_deleted:
            raise PaperNotFoundError(paper_id)
        return p

    def find_by_external_paper_id(
        self, *, workspace_id: str, external_paper_id: str
    ) -> Paper | None:
        """在工作区中查找已有的、未删除的外部论文。"""
        query = (
            select(Paper)
            .where(
                Paper.workspace_id == workspace_id,
                Paper.external_paper_id == external_paper_id,
                Paper.is_deleted.is_(False),
            )
            .order_by(Paper.created_at.desc())
            .limit(1)
        )
        return self.db.execute(query).scalars().first()

    def list(
        self,
        *,
        workspace_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Paper], int]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        base = select(Paper).where(
            Paper.workspace_id == workspace_id,
            Paper.is_deleted.is_(False),
        )
        items_q = base.order_by(Paper.created_at.desc()).limit(limit).offset(offset)
        total_q = select(func.count()).select_from(base.subquery())
        items = list(self.db.execute(items_q).scalars().all())
        total = int(self.db.execute(total_q).scalar() or 0)
        return items, total

# ----------------------------------------------------------------- 更新
    def update(self, paper_id: str, payload: PaperUpdate) -> Paper:
        paper = self.get(paper_id)
        data = payload.model_dump(exclude_unset=True)
        if not data:
            return paper
        for field, value in data.items():
            if field == "authors" and value is not None:
                value = list(value)
            setattr(paper, field, value)
        self.db.commit()
        self.db.refresh(paper)
        self.timeline_service.record(
            workspace_id=paper.workspace_id,
            event_type="paper.updated",
            subject_type="paper",
            subject_id=paper.id,
            payload={"fields": list(data.keys())},
        )
        logger.info("paper.updated", paper_id=paper.id, fields=list(data.keys()))
        return paper

# ----------------------------------------------------------------- 删除
    def soft_delete(self, paper_id: str) -> None:
        paper = self.get(paper_id)
        paper.is_deleted = True
        self.db.commit()
# 同步到搜索索引：软删除论文的向量必须从 Milvus 移除，避免后续检索返回它。
# 在数据库提交后再删除索引，保持回滚语义清晰；如果 Milvus 不可达则抛出异常，
# 使数据库与索引处于可由 reconcile 任务检测到的已知不一致状态。
        try:
            milvus_client.delete_by_paper(
                paper.id,
                workspace_id=paper.workspace_id,
            )
        except Exception as exc:
            logger.error(
                "paper.soft_delete_milvus_failed",
                paper_id=paper.id,
                error=str(exc),
            )
            raise
        self.timeline_service.record(
            workspace_id=paper.workspace_id,
            event_type="paper.deleted",
            subject_type="paper",
            subject_id=paper.id,
            payload={"title": paper.title},
        )
        logger.info("paper.soft_deleted", paper_id=paper_id)

# ------------------------------------------------------------- 辅助函数
    def _ensure_workspace_exists(self, workspace_id: str) -> None:
# 复用 WorkspaceService 实现 404 语义，避免创建第二个会话。
        WorkspaceService(self.db).get(workspace_id)

    @staticmethod
    def _validate_uuid(value: str) -> None:
        try:
            UUID(str(value))
        except (ValueError, TypeError) as e:
            raise PaperNotFoundError(value) from e
