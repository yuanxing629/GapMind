"""Timeline service layer.

Provides a single `record()` entry point used by other domain services to
emit events. The API layer only reads.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.timeline.models import TimelineEvent
from app.domains.timeline.schemas import TimelineRecordInternal

logger = get_logger(__name__)


class TimelineService:
    """Records and queries timeline events."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------ record
    def record(
        self,
        *,
        workspace_id: str,
        event_type: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        payload: dict | None = None,
        actor: str = "system",
        summary: str | None = None,
    ) -> TimelineEvent:
        """Persist a timeline event. Called from other services."""
        event = TimelineEvent(
            id=str(uuid4()),
            workspace_id=workspace_id,
            event_type=event_type,
            actor=actor,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=dict(payload or {}),
            summary=summary,
        )
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        logger.info(
            "timeline.recorded",
            event_id=event.id,
            workspace_id=workspace_id,
            event_type=event_type,
            subject_type=subject_type,
        )
        return event

    def record_payload(self, data: TimelineRecordInternal) -> TimelineEvent:
        """Record from a pre-validated TimelineRecordInternal object."""
        return self.record(
            workspace_id=data.workspace_id,
            event_type=data.event_type,
            subject_type=data.subject_type,
            subject_id=data.subject_id,
            payload=data.payload,
            actor=data.actor,
            summary=data.summary,
        )

    # ----------------------------------------------------------------- read
    def list(
        self,
        *,
        workspace_id: str,
        subject_type: str | None = None,
        subject_id: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[TimelineEvent], int]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        q = select(TimelineEvent).where(TimelineEvent.workspace_id == workspace_id)
        if subject_type is not None:
            q = q.where(TimelineEvent.subject_type == subject_type)
        if subject_id is not None:
            q = q.where(TimelineEvent.subject_id == subject_id)
        if event_type is not None:
            q = q.where(TimelineEvent.event_type == event_type)
        items_q = q.order_by(TimelineEvent.created_at.desc()).limit(limit).offset(offset)
        total_q = select(func.count()).select_from(q.subquery())
        items = list(self.db.execute(items_q).scalars().all())
        total = int(self.db.execute(total_q).scalar() or 0)
        return items, total
