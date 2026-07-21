"""Paper service layer.

Two creation paths:
  - create_from_metadata: JSON-only, no PDF (rare; useful for Semantic Scholar hits)
  - create_from_upload: saves PDF bytes via ArtifactService, then creates the Paper
    row pointing at the new Artifact.

Both paths record Timeline events through TimelineService.
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
from app.domains.workspace.service import WorkspaceNotFoundError, WorkspaceService

logger = get_logger(__name__)


class PaperNotFoundError(Exception):
    def __init__(self, paper_id: str) -> None:
        super().__init__(f"Paper not found: {paper_id}")
        self.paper_id = paper_id


class PaperAlreadyHasPdfError(Exception):
    """Raised when trying to attach a PDF to a paper that already has one."""

    def __init__(self, paper_id: str) -> None:
        super().__init__(f"Paper already has a PDF: {paper_id}")
        self.paper_id = paper_id


def _stem_filename(filename: str) -> str:
    """Return the filename without extension, used as a default paper title."""
    import os

    return os.path.splitext(filename)[0] or filename


class PaperService:
    """CRUD + upload for Paper."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.artifact_service = ArtifactService(db)
        # TimelineService is imported lazily to avoid a potential circular import
        # at module load time (timeline -> ... could eventually import paper).
        from app.domains.timeline.service import TimelineService

        self.timeline_service = TimelineService(db)

    # ------------------------------------------------------------ create
    def create_from_metadata(
        self,
        *,
        workspace_id: str,
        payload: PaperCreate,
        source: str = "manual",
        external_paper_id: str | None = None,
    ) -> Paper:
        """Create a paper with metadata only (no PDF)."""
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
        """Create a paper and save its PDF in one transaction.

        Order: save artifact -> create paper row pointing at it -> record timeline.
        If paper creation fails after artifact save, the artifact row is orphaned
        but soft-deleted to keep storage consistent (Phase 1b simplification).

        Metadata auto-fill: any field the caller left empty (None for scalars,
        empty list for authors) is filled from the PDF's embedded metadata
        dict (best-effort via PyMuPDF). Caller-supplied values always win.
        """
        self._ensure_workspace_exists(workspace_id)
        artifact = self.artifact_service.save_upload(
            workspace_id=workspace_id,
            filename=filename,
            content=content,
            mime_type=mime_type,
            kind="pdf",
        )

        # Best-effort metadata extraction. Fields the user already supplied
        # are not overwritten.
        pdf_meta = extract_metadata(content)
        title = payload.title or pdf_meta.title or _stem_filename(filename)
        authors = list(payload.authors) if payload.authors else list(pdf_meta.authors)
        year = payload.year if payload.year is not None else pdf_meta.year

        try:
            paper = Paper(
                id=str(uuid4()),
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
            # Mark artifact as soft-deleted so it doesn't leak into listings.
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

        # Spawn async parse_pdf task. Best-effort: if dispatch fails (e.g.
        # Redis is down), the paper stays in "pending" and the user can
        # retry from the UI later. We log but don't fail the upload.
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
        """Attach a PDF to an existing metadata-only Paper.

        Used when a paper was created via `create_from_metadata` and the user
        later obtains the PDF. Sets `primary_artifact_id` and best-effort fills
        any still-empty metadata fields from the PDF.
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
        )

        pdf_meta = extract_metadata(content)
        # Only fill fields that are still empty on the paper row.
        if not paper.title and pdf_meta.title:
            paper.title = pdf_meta.title
        if not paper.authors and pdf_meta.authors:
            paper.authors = list(pdf_meta.authors)
        if paper.year is None and pdf_meta.year is not None:
            paper.year = pdf_meta.year
        paper.primary_artifact_id = artifact.id
        # Paper now has a PDF -> mark pending so parse_pdf task picks it up.
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

        # Spawn async parse_pdf task (same as upload path).
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

    # ----------------------------------------------------------------- read
    def get(self, paper_id: str) -> Paper:
        self._validate_uuid(paper_id)
        p = self.db.get(Paper, paper_id)
        if p is None or p.is_deleted:
            raise PaperNotFoundError(paper_id)
        return p

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

    # ----------------------------------------------------------------- update
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

    # ----------------------------------------------------------------- delete
    def soft_delete(self, paper_id: str) -> None:
        paper = self.get(paper_id)
        paper.is_deleted = True
        self.db.commit()
        self.timeline_service.record(
            workspace_id=paper.workspace_id,
            event_type="paper.deleted",
            subject_type="paper",
            subject_id=paper.id,
            payload={"title": paper.title},
        )
        logger.info("paper.soft_deleted", paper_id=paper_id)

    # ------------------------------------------------------------- helpers
    def _ensure_workspace_exists(self, workspace_id: str) -> None:
        # Reuse WorkspaceService for 404 semantics without a second session.
        WorkspaceService(self.db).get(workspace_id)

    @staticmethod
    def _validate_uuid(value: str) -> None:
        try:
            UUID(str(value))
        except (ValueError, TypeError) as e:
            raise PaperNotFoundError(value) from e
