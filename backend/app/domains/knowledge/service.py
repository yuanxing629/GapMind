"""Knowledge service layer (read-only for Phase 1b).

Writes happen in Phase 3 via the extraction pipeline. This service exists
now so the API can list/get items and the frontend can show "Knowledge
Items" in the workspace detail page (empty for now).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.knowledge.models import (
    EvidenceSpan,
    KnowledgeItem,
    KnowledgeRelation,
)

logger = get_logger(__name__)


class KnowledgeItemNotFoundError(Exception):
    def __init__(self, item_id: str) -> None:
        super().__init__(f"Knowledge item not found: {item_id}")
        self.item_id = item_id


class KnowledgeService:
    """Read-only knowledge queries for Phase 1b."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # -------------------------------------------------------- knowledge items
    def get_item(self, item_id: str) -> KnowledgeItem:
        self._validate_uuid(item_id)
        item = self.db.get(KnowledgeItem, item_id)
        if item is None or item.is_deleted:
            raise KnowledgeItemNotFoundError(item_id)
        return item

    def list_items(
        self,
        *,
        workspace_id: str,
        type_filter: str | None = None,
        status_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[KnowledgeItem], int]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        q = select(KnowledgeItem).where(
            KnowledgeItem.workspace_id == workspace_id,
            KnowledgeItem.is_deleted.is_(False),
        )
        if type_filter:
            q = q.where(KnowledgeItem.type == type_filter)
        if status_filter:
            q = q.where(KnowledgeItem.status == status_filter)
        items_q = q.order_by(KnowledgeItem.created_at.desc()).limit(limit).offset(offset)
        total_q = select(func.count()).select_from(q.subquery())
        items = list(self.db.execute(items_q).scalars().all())
        total = int(self.db.execute(total_q).scalar() or 0)
        return items, total

    # -------------------------------------------------------- relations
    def list_relations(
        self,
        *,
        workspace_id: str,
        item_id: str | None = None,
        relation_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[KnowledgeRelation], int]:
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        q = select(KnowledgeRelation).where(
            KnowledgeRelation.workspace_id == workspace_id,
            KnowledgeRelation.is_deleted.is_(False),
        )
        if item_id:
            q = q.where(
                (KnowledgeRelation.source_id == item_id)
                | (KnowledgeRelation.target_id == item_id)
            )
        if relation_type:
            q = q.where(KnowledgeRelation.relation_type == relation_type)
        items_q = q.order_by(KnowledgeRelation.created_at.desc()).limit(limit).offset(offset)
        total_q = select(func.count()).select_from(q.subquery())
        items = list(self.db.execute(items_q).scalars().all())
        total = int(self.db.execute(total_q).scalar() or 0)
        return items, total

    # -------------------------------------------------------- evidence
    def list_evidence_for_item(self, item_id: str) -> list[EvidenceSpan]:
        self._validate_uuid(item_id)
        q = select(EvidenceSpan).where(EvidenceSpan.knowledge_item_id == item_id)
        return list(self.db.execute(q).scalars().all())

    @staticmethod
    def _validate_uuid(value: str) -> None:
        try:
            UUID(str(value))
        except (ValueError, TypeError) as e:
            raise KnowledgeItemNotFoundError(value) from e
