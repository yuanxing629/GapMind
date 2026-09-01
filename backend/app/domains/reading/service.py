"""Persistence operations for the reading library."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domains.artifact.models import Artifact
from app.domains.paper.models import Paper
from app.domains.reading.models import PaperAnnotation, ReadingItem
from app.domains.reading.schemas import (
    PaperAnnotationCreate,
    PaperAnnotationUpdate,
    ReadingProgressUpdate,
)
from app.domains.workspace.access import has_workspace_access
from app.domains.workspace.models import Workspace


class ReadingPaperNotFoundError(Exception):
    def __init__(self, paper_id: str) -> None:
        super().__init__(f"Reading paper not found: {paper_id}")
        self.paper_id = paper_id


class ReadingAnnotationNotFoundError(Exception):
    def __init__(self, annotation_id: str) -> None:
        super().__init__(f"Paper annotation not found: {annotation_id}")
        self.annotation_id = annotation_id


class ReadingService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_items(
        self,
        *,
        workspace_id: str | None,
        status: str | None,
        limit: int,
        offset: int,
        actor_id: str | None = None,
    ) -> tuple[list[tuple[ReadingItem, Paper, Workspace | None]], int]:
        conditions = [
            ReadingItem.is_deleted.is_(False),
            Paper.is_deleted.is_(False),
        ]
        if workspace_id:
            conditions.append(ReadingItem.workspace_id == workspace_id)
        if actor_id is not None:
            conditions.append(Workspace.owner_id == actor_id)
        if status:
            conditions.append(ReadingItem.status == status)

        base = (
            select(ReadingItem, Paper, Workspace)
            .join(Paper, Paper.id == ReadingItem.paper_id)
            .outerjoin(Workspace, Workspace.id == ReadingItem.workspace_id)
            .where(*conditions)
            .order_by(ReadingItem.last_read_at.desc().nullslast(), ReadingItem.updated_at.desc())
        )
        rows = list(self.db.execute(base.limit(limit).offset(offset)).all())
        total = int(
            self.db.scalar(
                select(func.count(ReadingItem.id))
                .join(Paper, Paper.id == ReadingItem.paper_id)
                .outerjoin(Workspace, Workspace.id == ReadingItem.workspace_id)
                .where(*conditions)
            )
            or 0
        )
        return rows, total

    def get_item(
        self, paper_id: str, *, actor_id: str | None = None
    ) -> tuple[ReadingItem, Paper, Workspace | None]:
        conditions = [
            ReadingItem.paper_id == paper_id,
            ReadingItem.is_deleted.is_(False),
            Paper.is_deleted.is_(False),
        ]
        if actor_id is not None:
            conditions.append(Workspace.owner_id == actor_id)
        row = self.db.execute(
            select(ReadingItem, Paper, Workspace)
            .join(Paper, Paper.id == ReadingItem.paper_id)
            .outerjoin(Workspace, Workspace.id == ReadingItem.workspace_id)
            .where(*conditions)
        ).first()
        if row is None:
            raise ReadingPaperNotFoundError(paper_id)
        return row

    def add_item(
        self, paper_id: str, *, actor_id: str | None = None
    ) -> tuple[ReadingItem, Paper, Workspace | None]:
        paper = self.db.get(Paper, paper_id)
        if paper is None or paper.is_deleted:
            raise ReadingPaperNotFoundError(paper_id)
        workspace = self.db.get(Workspace, paper.workspace_id)
        if actor_id is not None and (
            workspace is None
            or not has_workspace_access(self.db, workspace.id, actor_id)
        ):
            raise ReadingPaperNotFoundError(paper_id)
        item = self.db.execute(
            select(ReadingItem).where(ReadingItem.paper_id == paper_id)
        ).scalar_one_or_none()
        if item is None:
            item = ReadingItem(
                id=str(uuid4()),
                paper_id=paper.id,
                workspace_id=paper.workspace_id,
                status="unread",
                last_read_page=1,
                is_deleted=False,
            )
            self.db.add(item)
        else:
            item.workspace_id = paper.workspace_id
            item.is_deleted = False
        self.db.commit()
        self.db.refresh(item)
        return item, paper, workspace

    def remove_item(self, paper_id: str, *, actor_id: str | None = None) -> None:
        item, _, _ = self.get_item(paper_id, actor_id=actor_id)
        item.is_deleted = True
        self.db.commit()

    def update_progress(
        self, paper_id: str, payload: ReadingProgressUpdate, *, actor_id: str | None = None
    ) -> tuple[ReadingItem, Paper, Workspace | None]:
        item, paper, workspace = self.get_item(paper_id, actor_id=actor_id)
        item.last_read_page = payload.page_number
        item.status = payload.status or ("reading" if payload.page_number > 1 else item.status)
        item.last_read_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(item)
        return item, paper, workspace

    def list_annotations(
        self, paper_id: str, *, actor_id: str | None = None
    ) -> list[PaperAnnotation]:
        self.get_item(paper_id, actor_id=actor_id)
        return list(
            self.db.execute(
                select(PaperAnnotation)
                .where(
                    PaperAnnotation.paper_id == paper_id,
                    PaperAnnotation.is_deleted.is_(False),
                )
                .order_by(PaperAnnotation.page_number, PaperAnnotation.created_at)
            ).scalars().all()
        )

    def create_annotation(
        self, paper_id: str, payload: PaperAnnotationCreate, *, actor_id: str | None = None
    ) -> PaperAnnotation:
        item, paper, _ = self.get_item(paper_id, actor_id=actor_id)
        del item
        annotation = PaperAnnotation(
            id=str(uuid4()),
            paper_id=paper.id,
            workspace_id=paper.workspace_id,
            artifact_id=paper.primary_artifact_id,
            kind=payload.kind,
            page_number=payload.page_number,
            selected_text=payload.selected_text,
            note_content=payload.note_content.strip(),
            color=payload.color,
            rects=payload.rects,
            source_text_hash=payload.source_text_hash,
            is_deleted=False,
        )
        self.db.add(annotation)
        self.db.commit()
        self.db.refresh(annotation)
        return annotation

    def update_annotation(
        self, annotation_id: str, payload: PaperAnnotationUpdate, *, actor_id: str | None = None
    ) -> PaperAnnotation:
        annotation = self._get_annotation(annotation_id, actor_id=actor_id)
        for field in (
            "kind",
            "page_number",
            "selected_text",
            "note_content",
            "color",
            "rects",
            "source_text_hash",
        ):
            value = getattr(payload, field)
            if value is not None:
                if field == "note_content":
                    value = value.strip()
                setattr(annotation, field, value)
        self.db.commit()
        self.db.refresh(annotation)
        return annotation

    def remove_annotation(self, annotation_id: str, *, actor_id: str | None = None) -> None:
        annotation = self._get_annotation(annotation_id, actor_id=actor_id)
        annotation.is_deleted = True
        self.db.commit()

    def _get_annotation(
        self, annotation_id: str, *, actor_id: str | None = None
    ) -> PaperAnnotation:
        stmt = select(PaperAnnotation).where(
            PaperAnnotation.id == annotation_id,
            PaperAnnotation.is_deleted.is_(False),
        )
        if actor_id is not None:
            stmt = stmt.join(Workspace, Workspace.id == PaperAnnotation.workspace_id).where(
                Workspace.owner_id == actor_id
            )
        annotation = self.db.scalar(stmt)
        if annotation is None or annotation.is_deleted:
            raise ReadingAnnotationNotFoundError(annotation_id)
        return annotation


def annotation_artifact_exists(db: Session, annotation: PaperAnnotation) -> bool:
    """Small helper kept for future validation of annotation artifact versions."""
    if annotation.artifact_id is None:
        return True
    return db.get(Artifact, annotation.artifact_id) is not None
