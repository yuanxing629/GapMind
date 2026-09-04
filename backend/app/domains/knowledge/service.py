"""Knowledge service layer (Phase 3: read + write)."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.knowledge.models import (
    CanonicalEntity,
    EvidenceSpan,
    ExtractionRejection,
    ExtractionRun,
    KnowledgeItem,
    KnowledgeRelation,
    PaperMention,
)
from app.domains.knowledge.schemas import (
    EvidenceSpanCreate,
    ExtractionRejectionCreate,
    KnowledgeItemCreate,
    KnowledgeItemReview,
    KnowledgeRelationCreate,
)
from app.domains.paper.models import Paper

logger = get_logger(__name__)

GRAPH_MODE_TYPES = {
    "landscape": {"method", "task", "dataset"},
    "claims": {"claim", "limitation"},
    "evidence": {"method", "task", "dataset", "claim", "limitation", "evidence"},
}
WORKSPACE_ENTITY_TYPES = {"method", "task", "dataset"}

DISPLAY_TYPES = {
    "paper": "论文",
    "method": "方法",
    "task": "任务",
    "dataset": "数据集",
    "claim": "观点",
    "limitation": "局限",
    "evidence": "证据",
    "canonical_entity": "规范实体",
    "paper_mention": "原文提及",
}

RELATION_LABELS = {
    "supports": "支持", "contradicts": "反驳", "qualifies": "限定",
    "evaluates_on": "在数据集上评估", "extends": "扩展",
    "compares_with": "对比", "related_to": "相关", "contains": "包含知识",
    "canonicalizes": "对应规范实体", "mentioned_in": "包含原文提及",
    "refers_to": "指向实体", "evidences": "提供证据",
}


@dataclass
class GraphProjection:
    nodes: list
    edges: list
    total_nodes: int
    total_edges: int
    has_more: bool
    node_counts: dict[str, int]
    relation_counts: dict[str, int]
    workspace_counts: dict[str, int]
    truncated: bool = False
    truncation_reason: str | None = None


class KnowledgeItemNotFoundError(Exception):
    def __init__(self, item_id: str) -> None:
        super().__init__(f"Knowledge item not found: {item_id}")
        self.item_id = item_id


class ExtractionRunNotFoundError(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"Extraction run not found: {run_id}")
        self.run_id = run_id


class KnowledgeItemReviewError(ValueError):
    """Raised when a human review payload is rejected by the service.

    Subclasses ``ValueError`` so existing callers that catch generic value
    errors continue to work, but the new central exception handler maps it
    to a 422 with error code ``invalid_review``.
    """


class KnowledgeService:
    """Knowledge queries + writes for Phase 3."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # -------------------------------------------------------- knowledge items
    def get_item(self, item_id: str, *, workspace_id: str) -> KnowledgeItem:
        self._validate_uuid(item_id)
        item = self.db.scalar(
            select(KnowledgeItem).where(
                KnowledgeItem.id == item_id,
                KnowledgeItem.workspace_id == workspace_id,
                KnowledgeItem.is_deleted.is_(False),
            )
        )
        if item is None or item.is_deleted:
            raise KnowledgeItemNotFoundError(item_id)
        return item

    def list_items(
        self,
        *,
        workspace_id: str,
        type_filter: str | None = None,
        status_filter: str | None = None,
        paper_id: str | None = None,
        query_text: str | None = None,
        min_confidence: float | None = None,
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
        if paper_id:
            q = q.where(KnowledgeItem.paper_id == paper_id)
        if query_text:
            q = q.where(
                KnowledgeItem.canonical_name.ilike(f"%{query_text.strip()}%")
            )
        if min_confidence is not None:
            q = q.where(KnowledgeItem.confidence >= min_confidence)
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

    def graph(
        self,
        *,
        workspace_id: str,
        type_filter: str | None = None,
        paper_id: str | None = None,
        query_text: str | None = None,
        min_confidence: float | None = None,
        relation_type: str | None = None,
        limit: int = 250,
    ) -> tuple[list[KnowledgeItem], list[KnowledgeRelation], int, int]:
        """Return a bounded graph projection for a workspace.

        Relations are restricted to the selected node set so the frontend
        receives a self-contained graph and does not need to join IDs itself.
        """
        limit = max(1, min(limit, 500))
        item_query = select(KnowledgeItem).where(
            KnowledgeItem.workspace_id == workspace_id,
            KnowledgeItem.is_deleted.is_(False),
        )
        if type_filter:
            item_query = item_query.where(KnowledgeItem.type == type_filter)
        if paper_id:
            item_query = item_query.where(KnowledgeItem.paper_id == paper_id)
        if query_text:
            item_query = item_query.where(
                KnowledgeItem.canonical_name.ilike(f"%{query_text.strip()}%")
            )
        if min_confidence is not None:
            item_query = item_query.where(KnowledgeItem.confidence >= min_confidence)

        total_nodes_query = select(func.count()).select_from(item_query.subquery())
        total_nodes = int(self.db.execute(total_nodes_query).scalar() or 0)
        items = list(
            self.db.execute(
                item_query.order_by(KnowledgeItem.confidence.desc(), KnowledgeItem.created_at.desc())
                .limit(limit)
            ).scalars().all()
        )

        node_ids = [item.id for item in items]
        if not node_ids:
            return [], [], total_nodes, 0

        relation_query = select(KnowledgeRelation).where(
            KnowledgeRelation.workspace_id == workspace_id,
            KnowledgeRelation.is_deleted.is_(False),
            KnowledgeRelation.source_id.in_(node_ids),
            KnowledgeRelation.target_id.in_(node_ids),
        )
        if relation_type:
            relation_query = relation_query.where(
                KnowledgeRelation.relation_type == relation_type
            )
        relations = list(
            self.db.execute(
                relation_query.order_by(KnowledgeRelation.confidence.desc())
            ).scalars().all()
        )
        return items, relations, total_nodes, len(relations)

    def graph_projection(
        self,
        *,
        workspace_id: str,
        type_filter: str | None = None,
        paper_id: str | None = None,
        query_text: str | None = None,
        min_confidence: float | None = None,
        relation_type: str | None = None,
        status_filter: str | None = None,
        projection_mode: str = "all",
        limit: int = 100,
        offset: int = 0,
        edge_limit: int = 160,
        include_related_papers: bool = False,
        focus_node_id: str | None = None,
    ):
        """Build an appendable graph batch with exact aggregate metadata."""

        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        if projection_mode == "workspace":
            return self.workspace_graph_projection(
                workspace_id=workspace_id,
                type_filter=type_filter,
                paper_id=paper_id,
                query_text=query_text,
                min_confidence=min_confidence,
                relation_type=relation_type,
                status_filter=status_filter,
                node_limit=limit,
                edge_limit=edge_limit,
                offset=offset,
                include_related_papers=include_related_papers,
                focus_node_id=focus_node_id,
            )

        item_query = self._filtered_item_query(
            workspace_id=workspace_id,
            type_filter=type_filter,
            paper_id=paper_id,
            query_text=query_text,
            min_confidence=min_confidence,
            status_filter=status_filter,
            projection_mode=projection_mode,
        )
        total_knowledge = int(
            self.db.execute(select(func.count()).select_from(item_query.subquery())).scalar() or 0
        )
        items = list(
            self.db.execute(
                item_query.order_by(
                    KnowledgeItem.confidence.desc(), KnowledgeItem.created_at.desc()
                ).limit(limit).offset(offset)
            ).scalars().all()
        )
        nodes, edges, structural_total, mention_truncated = self._build_projection(
            workspace_id=workspace_id,
            items=items,
            relation_type=relation_type,
            node_limit=limit,
            include_mentions=projection_mode in {"all", "evidence"},
            include_entities=projection_mode != "claims",
            strict_paper_ids={paper_id} if paper_id and not include_related_papers else None,
        )
        node_counts, relation_counts, total_nodes, total_edges = self._graph_aggregate_counts(
            workspace_id=workspace_id,
            item_query=item_query,
            relation_type=relation_type,
            include_mentions=projection_mode in {"all", "evidence"},
            include_entities=projection_mode != "claims",
            strict_paper_ids={paper_id} if paper_id and not include_related_papers else None,
        )
        # Pagination advances the primary KnowledgeItem cursor. Structural
        # nodes are attached to each batch, so has_more must never promise a
        # page that the client cannot actually request.
        has_more = offset + len(items) < total_knowledge
        return GraphProjection(
            nodes=nodes,
            edges=edges,
            total_nodes=total_nodes,
            total_edges=total_edges,
            has_more=has_more,
            node_counts=node_counts,
            relation_counts=relation_counts,
            workspace_counts=self._workspace_graph_counts(workspace_id),
            truncated=has_more,
            truncation_reason="node_limit" if has_more else None,
        )

    def graph_neighbors(
        self,
        *,
        workspace_id: str,
        node_id: str,
        depth: int = 1,
        limit: int = 100,
        relation_type: str | None = None,
    ):
        """Return a bounded neighborhood for a graph node.

        Node IDs use ``paper:``, ``entity:``, ``mention:`` prefixes for the
        structural layers; knowledge item IDs remain unprefixed for backward
        compatibility with the original graph API.
        """
        from app.domains.knowledge.schemas import (
            KnowledgeGraphEdgeRead,
            KnowledgeGraphNodeRead,
        )

        limit = max(1, min(limit, 200))
        depth = max(1, min(depth, 2))
        kind, raw_id = self._split_graph_node_id(node_id)
        items: list[KnowledgeItem] = []
        extra_paper_ids: set[str] = set()
        extra_entity_ids: set[str] = set()
        if kind == "knowledge":
            seed = self.get_item(raw_id, workspace_id=workspace_id)
            visited = {seed.id}
            frontier = {seed.id}
            for _ in range(depth):
                relation_query = select(KnowledgeRelation).where(
                    KnowledgeRelation.workspace_id == workspace_id,
                    KnowledgeRelation.is_deleted.is_(False),
                    or_(
                        KnowledgeRelation.source_id.in_(frontier),
                        KnowledgeRelation.target_id.in_(frontier),
                    ),
                )
                relation_ids = set()
                for relation in self.db.execute(relation_query).scalars().all():
                    relation_ids.add(relation.source_id)
                    relation_ids.add(relation.target_id)
                frontier = relation_ids - visited
                visited.update(relation_ids)
                if not frontier:
                    break
            items = list(
                self.db.execute(
                    select(KnowledgeItem).where(
                        KnowledgeItem.id.in_(list(visited)),
                        KnowledgeItem.workspace_id == workspace_id,
                        KnowledgeItem.is_deleted.is_(False),
                    )
                ).scalars().all()
            )
        elif kind == "paper":
            extra_paper_ids.add(raw_id)
            items = list(
                self.db.execute(
                    select(KnowledgeItem).where(
                        KnowledgeItem.workspace_id == workspace_id,
                        KnowledgeItem.paper_id == raw_id,
                        KnowledgeItem.is_deleted.is_(False),
                    ).order_by(KnowledgeItem.confidence.desc()).limit(limit)
                ).scalars().all()
            )
        elif kind == "entity":
            extra_entity_ids.add(raw_id)
            items = list(
                self.db.execute(
                    select(KnowledgeItem).where(
                        KnowledgeItem.workspace_id == workspace_id,
                        KnowledgeItem.canonical_entity_id == raw_id,
                        KnowledgeItem.is_deleted.is_(False),
                    ).order_by(KnowledgeItem.confidence.desc()).limit(limit)
                ).scalars().all()
            )
        elif kind == "mention":
            mention = self.db.scalar(
                select(PaperMention).where(
                    PaperMention.id == raw_id,
                    PaperMention.workspace_id == workspace_id,
                    PaperMention.is_deleted.is_(False),
                )
            )
            if mention is None:
                raise KnowledgeItemNotFoundError(raw_id)
            extra_paper_ids.add(mention.paper_id)
            extra_entity_ids.add(mention.canonical_entity_id)
            if mention.knowledge_item_id:
                item = self.db.scalar(
                    select(KnowledgeItem).where(
                        KnowledgeItem.id == mention.knowledge_item_id,
                        KnowledgeItem.workspace_id == workspace_id,
                        KnowledgeItem.is_deleted.is_(False),
                    )
                )
                if item:
                    items = [item]
        else:
            raise KnowledgeItemNotFoundError(node_id)

        nodes, edges, _, _ = self._build_projection(
            workspace_id=workspace_id,
            items=items,
            relation_type=relation_type,
            node_limit=limit,
            extra_paper_ids=extra_paper_ids,
            extra_entity_ids=extra_entity_ids,
            forced_mention_id=raw_id if kind == "mention" else None,
            include_mentions=True,
        )
        return nodes, edges

    def workspace_graph_projection(
        self,
        *,
        workspace_id: str,
        type_filter: str | None = None,
        paper_id: str | None = None,
        query_text: str | None = None,
        min_confidence: float | None = None,
        relation_type: str | None = None,
        status_filter: str | None = None,
        node_limit: int = 80,
        edge_limit: int = 160,
        offset: int = 0,
        include_related_papers: bool = False,
        focus_node_id: str | None = None,
        focus_depth: int = 1,
    ) -> GraphProjection:
        """Project a workspace map around papers and shared canonical entities.

        This is deliberately a read-only projection over the existing paper,
        knowledge-item, mention, relation, and evidence tables. Claims and
        limitations are kept out of the cross-paper entity projection even if
        a malformed or legacy row happens to carry a canonical entity id.
        """
        from app.domains.knowledge.schemas import (
            KnowledgeGraphEdgeRead,
            KnowledgeGraphNodeRead,
        )

        node_limit = max(1, min(node_limit, 200))
        edge_limit = max(1, min(edge_limit, 400))
        offset = max(0, offset)

        papers = list(self.db.execute(
            select(Paper).where(
                Paper.workspace_id == workspace_id,
                Paper.is_deleted.is_(False),
            ).order_by(Paper.created_at.desc())
        ).scalars().all())
        paper_map = {paper.id: paper for paper in papers}

        entities = list(self.db.execute(
            select(CanonicalEntity).where(
                CanonicalEntity.workspace_id == workspace_id,
                CanonicalEntity.is_deleted.is_(False),
                CanonicalEntity.type.in_(WORKSPACE_ENTITY_TYPES),
            )
        ).scalars().all())
        entity_map = {entity.id: entity for entity in entities}

        all_items = list(self.db.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.workspace_id == workspace_id,
                KnowledgeItem.is_deleted.is_(False),
            )
        ).scalars().all())
        all_mentions = list(self.db.execute(
            select(PaperMention).where(
                PaperMention.workspace_id == workspace_id,
                PaperMention.is_deleted.is_(False),
            )
        ).scalars().all())

        def item_matches(item: KnowledgeItem) -> bool:
            if item.paper_id not in paper_map or item.type not in WORKSPACE_ENTITY_TYPES:
                return False
            if type_filter and item.type != type_filter:
                return False
            if status_filter and item.status != status_filter:
                return False
            if min_confidence is not None and item.confidence < min_confidence:
                return False
            return True

        matched_items = [item for item in all_items if item_matches(item)]
        matched_item_ids = {item.id for item in matched_items}

        def mention_matches(mention: PaperMention) -> bool:
            if mention.paper_id not in paper_map or mention.canonical_entity_id not in entity_map:
                return False
            if type_filter and entity_map[mention.canonical_entity_id].type != type_filter:
                return False
            if min_confidence is not None and mention.confidence < min_confidence:
                return False
            # A status filter describes knowledge-item review state. Mentions
            # are evidence locations and do not acquire that state on their own.
            if status_filter and mention.knowledge_item_id not in matched_item_ids:
                return False
            return True

        matched_mentions = [mention for mention in all_mentions if mention_matches(mention)]

        if paper_id:
            scope_paper_ids = {paper_id} if paper_id in paper_map else set()
        elif type_filter or status_filter or min_confidence is not None:
            scope_paper_ids = {
                item.paper_id for item in matched_items if item.paper_id
            }
            scope_paper_ids.update(mention.paper_id for mention in matched_mentions)
        else:
            scope_paper_ids = set(paper_map)

        if paper_id and include_related_papers and scope_paper_ids:
            seed_entity_ids = {
                item.canonical_entity_id
                for item in matched_items
                if item.paper_id in scope_paper_ids and item.canonical_entity_id in entity_map
            }
            seed_entity_ids.update(
                mention.canonical_entity_id
                for mention in matched_mentions
                if mention.paper_id in scope_paper_ids
            )
            scope_paper_ids.update(
                mention.paper_id
                for mention in all_mentions
                if (
                    not mention.is_deleted
                    and mention.workspace_id == workspace_id
                    and mention.canonical_entity_id in seed_entity_ids
                    and mention.paper_id in paper_map
                )
            )
            scope_paper_ids.update(
                item.paper_id
                for item in all_items
                if (
                    not item.is_deleted
                    and item.workspace_id == workspace_id
                    and item.canonical_entity_id in seed_entity_ids
                    and item.paper_id in paper_map
                    and item.type in WORKSPACE_ENTITY_TYPES
                )
            )

        term = query_text.strip().lower() if query_text else ""
        matching_entity_ids: set[str] = set()
        matching_paper_ids: set[str] = set()
        matching_item_paper_ids: set[str] = set()
        if term:
            matching_entity_ids = {
                entity.id
                for entity in entities
                if term in entity.canonical_name.lower()
                or any(term in alias.lower() for alias in (entity.aliases or []))
            }
            matching_paper_ids = {
                paper.id for paper in papers if term in paper.title.lower()
            }
            matching_item_paper_ids = {
                item.paper_id
                for item in all_items
                if item.paper_id in paper_map
                and item.type in WORKSPACE_ENTITY_TYPES
                and term in item.canonical_name.lower()
            }
            matching_entity_ids.update(
                item.canonical_entity_id
                for item in all_items
                if item.paper_id in paper_map
                and item.type in WORKSPACE_ENTITY_TYPES
                and term in item.canonical_name.lower()
                and item.canonical_entity_id in entity_map
            )
            matching_paper_ids.update(matching_item_paper_ids)
            matching_paper_ids.update(
                item.paper_id
                for item in all_items
                if item.paper_id in paper_map
                and item.canonical_entity_id in matching_entity_ids
            )
            matching_paper_ids.update(
                mention.paper_id
                for mention in all_mentions
                if mention.paper_id in paper_map
                and mention.canonical_entity_id in matching_entity_ids
            )
            matching_entity_ids.update(
                item.canonical_entity_id
                for item in all_items
                if item.paper_id in matching_paper_ids
                and item.type in WORKSPACE_ENTITY_TYPES
                and item.canonical_entity_id in entity_map
            )
            matching_entity_ids.update(
                mention.canonical_entity_id
                for mention in all_mentions
                if mention.paper_id in matching_paper_ids
                and mention.canonical_entity_id in entity_map
            )
            scope_paper_ids &= matching_paper_ids

        visible_items = [
            item for item in matched_items
            if item.paper_id in scope_paper_ids
            and (not term or item.canonical_entity_id in matching_entity_ids)
        ]
        visible_mentions = [
            mention for mention in matched_mentions
            if mention.paper_id in scope_paper_ids
            and (not term or mention.canonical_entity_id in matching_entity_ids)
        ]
        def item_has_valid_entity(item: KnowledgeItem) -> bool:
            return (
                item.canonical_entity_id in entity_map
                and entity_map[item.canonical_entity_id].type == item.type
            )

        visible_entity_ids = {
            item.canonical_entity_id
            for item in visible_items
            if item_has_valid_entity(item)
        }
        visible_entity_ids.update(
            mention.canonical_entity_id
            for mention in visible_mentions
            if mention.canonical_entity_id in entity_map
        )
        visible_paper_ids = {
            paper_id_value for paper_id_value in scope_paper_ids if paper_id_value in paper_map
        }

        visible_item_ids = {item.id for item in visible_items}
        evidence_by_item: dict[str, set[str]] = defaultdict(set)
        evidence_rows = list(self.db.execute(
            select(EvidenceSpan).where(
                EvidenceSpan.workspace_id == workspace_id,
                EvidenceSpan.is_deleted.is_(False),
                EvidenceSpan.knowledge_item_id.in_(list(visible_item_ids))
                if visible_item_ids else False,
            )
        ).scalars().all())
        for evidence in evidence_rows:
            evidence_by_item[evidence.knowledge_item_id].add(evidence.id)

        item_ids_by_entity: dict[str, set[str]] = defaultdict(set)
        paper_ids_by_entity: dict[str, set[str]] = defaultdict(set)
        item_confidence_by_entity: dict[str, list[float]] = defaultdict(list)
        for item in visible_items:
            if not item_has_valid_entity(item) or item.canonical_entity_id not in visible_entity_ids:
                continue
            item_ids_by_entity[item.canonical_entity_id].add(item.id)
            if item.paper_id:
                paper_ids_by_entity[item.canonical_entity_id].add(item.paper_id)
            item_confidence_by_entity[item.canonical_entity_id].append(item.confidence)

        mention_ids_by_entity: dict[str, set[str]] = defaultdict(set)
        mention_count_by_paper_entity: dict[tuple[str, str], int] = defaultdict(int)
        mention_confidence_by_entity: dict[str, list[float]] = defaultdict(list)
        for mention in visible_mentions:
            if mention.canonical_entity_id not in visible_entity_ids:
                continue
            mention_ids_by_entity[mention.canonical_entity_id].add(mention.id)
            paper_ids_by_entity[mention.canonical_entity_id].add(mention.paper_id)
            mention_count_by_paper_entity[(mention.paper_id, mention.canonical_entity_id)] += 1
            mention_confidence_by_entity[mention.canonical_entity_id].append(mention.confidence)

        entity_stats: dict[str, dict] = {}
        for entity_id in visible_entity_ids:
            entity = entity_map[entity_id]
            item_ids = item_ids_by_entity[entity_id]
            supporting_paper_ids = sorted(paper_ids_by_entity[entity_id])
            entity_stats[entity_id] = {
                "paper_ids": supporting_paper_ids,
                "item_ids": item_ids,
                "mention_count": len(mention_ids_by_entity[entity_id]),
                "knowledge_item_count": len(item_ids),
                "evidence_count": len({
                    evidence_id
                    for item_id in item_ids
                    for evidence_id in evidence_by_item.get(item_id, set())
                }),
                "confirmed_item_count": sum(
                    1 for item in visible_items
                    if item.id in item_ids and item.status == "human_confirmed"
                ),
                "confidence": max(
                    item_confidence_by_entity[entity_id]
                    + mention_confidence_by_entity[entity_id]
                    + [0.0]
                ),
                "entity": entity,
            }

        paper_stats: dict[str, dict] = defaultdict(
            lambda: {
                "item_ids": set(),
                "mention_count": 0,
                "evidence_ids": set(),
                "confirmed_item_count": 0,
            }
        )
        for item in visible_items:
            if item.paper_id in visible_paper_ids:
                paper_stats[item.paper_id]["item_ids"].add(item.id)
                paper_stats[item.paper_id]["evidence_ids"].update(evidence_by_item.get(item.id, set()))
                if item.status == "human_confirmed":
                    paper_stats[item.paper_id]["confirmed_item_count"] += 1
        for mention in visible_mentions:
            if mention.paper_id in visible_paper_ids:
                paper_stats[mention.paper_id]["mention_count"] += 1

        nodes: list[KnowledgeGraphNodeRead] = []
        for paper_id_value in visible_paper_ids:
            paper = paper_map[paper_id_value]
            stats = paper_stats[paper_id_value]
            nodes.append(KnowledgeGraphNodeRead(
                id=f"paper:{paper.id}", label=paper.title, type="paper",
                workspace_id=workspace_id, paper_id=paper.id, confidence=1.0, status=paper.parse_status,
                content={
                    "year": paper.year,
                    "source": paper.source,
                    "parse_status": paper.parse_status,
                    "extract_status": paper.extract_status,
                    "has_pdf": paper.primary_artifact_id is not None,
                },
                node_kind="paper", paper_title=paper.title,
                display_label=paper.title, display_type=DISPLAY_TYPES["paper"],
                paper_count=1, mention_count=stats["mention_count"],
                knowledge_item_count=len(stats["item_ids"]),
                evidence_count=len(stats["evidence_ids"]),
                confirmed_item_count=stats["confirmed_item_count"],
                supporting_paper_ids=[paper.id], supporting_paper_ids_truncated=False,
                review_status=paper.parse_status,
            ))
        for _entity_id, stats in entity_stats.items():
            entity = stats["entity"]
            nodes.append(KnowledgeGraphNodeRead(
                id=f"entity:{entity.id}", label=entity.canonical_name,
                type="canonical_entity", workspace_id=workspace_id, confidence=stats["confidence"],
                status=entity.status, content={"aliases": entity.aliases or [], "projection": "workspace"},
                node_kind="canonical_entity", canonical_entity_id=entity.id,
                entity_type=entity.type, display_label=entity.canonical_name,
                display_type=DISPLAY_TYPES.get(entity.type, entity.type),
                importance_score=round(min(
                    1.0,
                    len(stats["paper_ids"]) / 5 if stats["paper_ids"] else 0.0,
                ), 4),
                evidence_count=stats["evidence_count"],
                paper_count=len(stats["paper_ids"]),
                mention_count=stats["mention_count"],
                knowledge_item_count=stats["knowledge_item_count"],
                confirmed_item_count=stats["confirmed_item_count"],
                aliases=sorted(set(entity.aliases or [])),
                supporting_paper_ids=stats["paper_ids"],
                supporting_paper_ids_truncated=False,
                review_status=entity.status,
            ))

        item_to_entity = {
            item.id: item.canonical_entity_id
            for item in visible_items
            if item_has_valid_entity(item) and item.canonical_entity_id in visible_entity_ids
        }
        visible_item_map = {item.id: item for item in visible_items}
        pair_stats: dict[tuple[str, str], dict] = defaultdict(
            lambda: {"item_ids": set(), "mention_count": 0, "evidence_ids": set(), "confidence": 0.0}
        )
        for item in visible_items:
            if item.paper_id and item.id in item_to_entity:
                stats = pair_stats[(item.paper_id, item_to_entity[item.id])]
                stats["item_ids"].add(item.id)
                stats["evidence_ids"].update(evidence_by_item.get(item.id, set()))
                stats["confidence"] = max(stats["confidence"], item.confidence)
        for mention in visible_mentions:
            stats = pair_stats[(mention.paper_id, mention.canonical_entity_id)]
            stats["mention_count"] += 1
            stats["confidence"] = max(stats["confidence"], mention.confidence)

        edges: list[KnowledgeGraphEdgeRead] = []
        for (paper_id_value, entity_id), stats in pair_stats.items():
            occurrence_count = max(stats["mention_count"], len(stats["item_ids"]))
            if occurrence_count == 0:
                continue
            edges.append(KnowledgeGraphEdgeRead(
                id=f"paper_entity:{paper_id_value}:{entity_id}",
                source=f"paper:{paper_id_value}", target=f"entity:{entity_id}",
                relation_type="paper_entity", confidence=stats["confidence"],
                payload={"projection": "workspace"}, display_label="涉及实体",
                relation_group="structural", occurrence_count=occurrence_count,
                paper_count=1, evidence_count=len(stats["evidence_ids"]),
                supporting_paper_ids=[paper_id_value],
                supporting_item_ids=sorted(stats["item_ids"]),
            ))

        relations = list(self.db.execute(
            select(KnowledgeRelation).where(
                KnowledgeRelation.workspace_id == workspace_id,
                KnowledgeRelation.is_deleted.is_(False),
                KnowledgeRelation.source_id.in_(list(visible_item_ids))
                if visible_item_ids else False,
                KnowledgeRelation.target_id.in_(list(visible_item_ids))
                if visible_item_ids else False,
            )
        ).scalars().all())
        if relation_type:
            relations = [relation for relation in relations if relation.relation_type == relation_type]
        relation_stats: dict[tuple[str, str, str], dict] = defaultdict(
            lambda: {
                "relation_ids": [], "paper_ids": set(), "item_ids": set(),
                "evidence_ids": set(), "confidence": 0.0,
            }
        )
        for relation in relations:
            source_entity_id = item_to_entity.get(relation.source_id)
            target_entity_id = item_to_entity.get(relation.target_id)
            if not source_entity_id or not target_entity_id or source_entity_id == target_entity_id:
                continue
            key = (source_entity_id, target_entity_id, relation.relation_type)
            stats = relation_stats[key]
            stats["relation_ids"].append(relation.id)
            stats["item_ids"].update({relation.source_id, relation.target_id})
            for item_id in (relation.source_id, relation.target_id):
                item = visible_item_map.get(item_id)
                if item and item.paper_id:
                    stats["paper_ids"].add(item.paper_id)
                stats["evidence_ids"].update(evidence_by_item.get(item_id, set()))
            stats["confidence"] = max(stats["confidence"], relation.confidence)

        for (source_entity_id, target_entity_id, rel_type), stats in relation_stats.items():
            edges.append(KnowledgeGraphEdgeRead(
                id=f"entity_relation:{source_entity_id}:{target_entity_id}:{rel_type}",
                source=f"entity:{source_entity_id}", target=f"entity:{target_entity_id}",
                relation_type=rel_type, confidence=stats["confidence"],
                payload={
                    "projection": "workspace",
                    "source_relation_ids": sorted(stats["relation_ids"]),
                },
                display_label=RELATION_LABELS.get(rel_type, rel_type),
                relation_group="semantic", occurrence_count=len(stats["relation_ids"]),
                paper_count=len(stats["paper_ids"]),
                evidence_count=len(stats["evidence_ids"]),
                supporting_paper_ids=sorted(stats["paper_ids"]),
                supporting_item_ids=sorted(stats["item_ids"]),
            ))

        node_by_id = {node.id: node for node in nodes}
        if focus_node_id:
            if focus_node_id not in node_by_id:
                raise KnowledgeItemNotFoundError(focus_node_id)
            focused_ids = {focus_node_id}
            for _ in range(max(1, min(focus_depth, 2))):
                focused_ids.update(
                    edge.target if edge.source in focused_ids else edge.source
                    for edge in edges
                    if edge.source in focused_ids or edge.target in focused_ids
                )
            nodes = [node for node in nodes if node.id in focused_ids]
            edges = [
                edge for edge in edges
                if edge.source in focused_ids and edge.target in focused_ids
            ]

        # Alternate entity and paper rows so a small first page still gives
        # the workspace map both sides of its intended visual vocabulary.
        entity_nodes = sorted(
            [node for node in nodes if node.node_kind == "canonical_entity"],
            key=lambda node: (
                -node.paper_count, -node.confirmed_item_count,
                -node.evidence_count, -node.mention_count, -node.confidence, node.label.lower(),
            ),
        )
        paper_nodes = sorted(
            [node for node in nodes if node.node_kind == "paper"],
            key=lambda node: (-node.knowledge_item_count, -node.evidence_count, node.label.lower()),
        )
        ordered_nodes: list[KnowledgeGraphNodeRead] = []
        for index in range(max(len(entity_nodes), len(paper_nodes))):
            if index < len(entity_nodes):
                ordered_nodes.append(entity_nodes[index])
            if index < len(paper_nodes):
                ordered_nodes.append(paper_nodes[index])
        nodes = ordered_nodes

        node_degree: dict[str, int] = defaultdict(int)
        for edge in edges:
            node_degree[edge.source] += 1
            node_degree[edge.target] += 1
        nodes = [node.model_copy(update={
            "relation_count": node_degree.get(node.id, 0),
            "importance_score": round(min(
                1.0,
                node.confidence * 0.35
                + min(1.0, node.paper_count / 5) * 0.35
                + min(1.0, node.evidence_count / 10) * 0.2
                + min(1.0, node.confirmed_item_count / 5) * 0.1,
            ), 4),
        }) for node in nodes]

        node_counts = {
            "paper": len(paper_nodes),
            "canonical_entity": len(entity_nodes),
        }
        relation_counts: dict[str, int] = defaultdict(int)
        for edge in edges:
            relation_counts[edge.relation_type] += 1
        page_nodes = nodes[offset:offset + node_limit]
        page_node_ids = {node.id for node in page_nodes}
        page_edges = [
            edge for edge in edges
            if edge.source in page_node_ids and edge.target in page_node_ids
        ]
        edge_has_more = len(page_edges) > edge_limit
        page_edges = page_edges[:edge_limit]
        node_has_more = offset + len(page_nodes) < len(nodes)
        truncation_reasons = []
        if node_has_more:
            truncation_reasons.append("node_limit")
        if edge_has_more:
            truncation_reasons.append("edge_limit")
        return GraphProjection(
            nodes=page_nodes,
            edges=page_edges,
            total_nodes=len(nodes),
            total_edges=len(edges),
            has_more=node_has_more,
            node_counts=node_counts,
            relation_counts=dict(relation_counts),
            workspace_counts=self._workspace_graph_counts(workspace_id),
            truncated=bool(truncation_reasons),
            truncation_reason="+".join(truncation_reasons) or None,
        )

    def _filtered_item_query(
        self,
        *,
        workspace_id: str,
        type_filter: str | None,
        paper_id: str | None,
        query_text: str | None,
        min_confidence: float | None,
        status_filter: str | None = None,
        projection_mode: str = "all",
    ):
        query = select(KnowledgeItem).where(
            KnowledgeItem.workspace_id == workspace_id,
            KnowledgeItem.is_deleted.is_(False),
        )
        if type_filter:
            query = query.where(KnowledgeItem.type == type_filter)
        if paper_id:
            query = query.where(KnowledgeItem.paper_id == paper_id)
        if query_text:
            query = query.where(KnowledgeItem.canonical_name.ilike(f"%{query_text.strip()}%"))
        if min_confidence is not None:
            query = query.where(KnowledgeItem.confidence >= min_confidence)
        if status_filter:
            query = query.where(KnowledgeItem.status == status_filter)
        mode_types = GRAPH_MODE_TYPES.get(projection_mode)
        if mode_types:
            query = query.where(KnowledgeItem.type.in_(mode_types))
        return query

    def _build_projection(
        self,
        *,
        workspace_id: str,
        items: list[KnowledgeItem],
        relation_type: str | None,
        node_limit: int,
        extra_paper_ids: set[str] | None = None,
        extra_entity_ids: set[str] | None = None,
        forced_mention_id: str | None = None,
        include_mentions: bool = True,
        include_entities: bool = True,
        strict_paper_ids: set[str] | None = None,
    ):
        from app.domains.knowledge.schemas import (
            KnowledgeGraphEdgeRead,
            KnowledgeGraphNodeRead,
        )

        paper_ids = {item.paper_id for item in items if item.paper_id}
        paper_ids.update(extra_paper_ids or set())
        entity_ids = {item.canonical_entity_id for item in items if item.canonical_entity_id} if include_entities else set()
        if include_entities:
            entity_ids.update(extra_entity_ids or set())
        papers = list(
            self.db.execute(
                select(Paper).where(
                    Paper.id.in_(list(paper_ids)),
                    Paper.workspace_id == workspace_id,
                    Paper.is_deleted.is_(False),
                )
            ).scalars().all()
        ) if paper_ids else []
        entities = list(
            self.db.execute(
                select(CanonicalEntity).where(
                    CanonicalEntity.id.in_(list(entity_ids)),
                    CanonicalEntity.workspace_id == workspace_id,
                    CanonicalEntity.is_deleted.is_(False),
                )
            ).scalars().all()
        ) if entity_ids else []
        mention_query = select(PaperMention).where(
            PaperMention.workspace_id == workspace_id,
            PaperMention.is_deleted.is_(False),
            PaperMention.paper_id.in_(select(Paper.id).where(
                Paper.workspace_id == workspace_id,
                Paper.is_deleted.is_(False),
            )),
            PaperMention.canonical_entity_id.in_(select(CanonicalEntity.id).where(
                CanonicalEntity.workspace_id == workspace_id,
                CanonicalEntity.is_deleted.is_(False),
            )),
        )
        if strict_paper_ids is not None:
            mention_query = mention_query.where(
                PaperMention.paper_id.in_(list(strict_paper_ids))
            )
        elif paper_ids or entity_ids:
            mention_query = mention_query.where(
                or_(
                    PaperMention.paper_id.in_(list(paper_ids)) if paper_ids else False,
                    PaperMention.canonical_entity_id.in_(list(entity_ids)) if entity_ids else False,
                )
            )
        if forced_mention_id:
            mention_query = mention_query.where(PaperMention.id == forced_mention_id)
        mention_total = int(
            self.db.execute(select(func.count()).select_from(mention_query.subquery())).scalar() or 0
        ) if include_mentions else 0
        mentions = list(
            self.db.execute(
                mention_query.order_by(PaperMention.confidence.desc()).limit(max(1, min(node_limit * 2, 500)))
            ).scalars().all()
        ) if include_mentions and (paper_ids or entity_ids or forced_mention_id) else []

        # A paper or forced mention can introduce structural endpoints that
        # were not present on the primary KnowledgeItems. Load those endpoints
        # before creating edges so every projection remains self-contained.
        loaded_paper_ids = {paper.id for paper in papers}
        missing_paper_ids = {mention.paper_id for mention in mentions} - loaded_paper_ids
        if missing_paper_ids:
            papers.extend(self.db.execute(select(Paper).where(
                Paper.id.in_(missing_paper_ids),
                Paper.workspace_id == workspace_id,
                Paper.is_deleted.is_(False),
            )).scalars().all())
        loaded_entity_ids = {entity.id for entity in entities}
        missing_entity_ids = {
            mention.canonical_entity_id for mention in mentions
        } - loaded_entity_ids
        if missing_entity_ids:
            entities.extend(self.db.execute(select(CanonicalEntity).where(
                CanonicalEntity.id.in_(missing_entity_ids),
                CanonicalEntity.workspace_id == workspace_id,
                CanonicalEntity.is_deleted.is_(False),
            )).scalars().all())

        item_ids = [item.id for item in items]
        evidence_counts: dict[str, int] = {}
        if item_ids:
            evidence_counts = {
                item_id: int(count)
                for item_id, count in self.db.execute(
                    select(EvidenceSpan.knowledge_item_id, func.count(EvidenceSpan.id))
                    .where(
                        EvidenceSpan.workspace_id == workspace_id,
                        EvidenceSpan.is_deleted.is_(False),
                        EvidenceSpan.knowledge_item_id.in_(item_ids),
                    )
                    .group_by(EvidenceSpan.knowledge_item_id)
                ).all()
            }

        nodes: list[KnowledgeGraphNodeRead] = []
        for item in items:
            nodes.append(KnowledgeGraphNodeRead(
                id=item.id,
                label=item.canonical_name,
                type=item.type,
                workspace_id=item.workspace_id,
                paper_id=item.paper_id,
                canonical_entity_id=item.canonical_entity_id,
                confidence=item.confidence,
                status=item.status,
                content=item.content,
                node_kind="knowledge",
                knowledge_item_id=item.id,
                display_label=item.canonical_name,
                display_type=DISPLAY_TYPES.get(item.type, item.type),
                evidence_count=evidence_counts.get(item.id, 0),
                paper_count=1 if item.paper_id else 0,
                review_status=item.status,
            ))
        paper_map = {paper.id: paper for paper in papers}
        for paper in papers:
            nodes.append(KnowledgeGraphNodeRead(
                id=f"paper:{paper.id}", label=paper.title, type="paper",
                workspace_id=workspace_id, paper_id=paper.id, confidence=1.0, status=paper.parse_status,
                content={
                    "year": paper.year,
                    "source": paper.source,
                    "parse_status": paper.parse_status,
                    "extract_status": paper.extract_status,
                    "has_pdf": paper.primary_artifact_id is not None,
                },
                node_kind="paper", paper_title=paper.title,
                display_label=paper.title, display_type=DISPLAY_TYPES["paper"],
                paper_count=1, review_status=paper.parse_status,
            ))
        for entity in entities:
            nodes.append(KnowledgeGraphNodeRead(
                id=f"entity:{entity.id}", label=entity.canonical_name,
                type="canonical_entity", workspace_id=workspace_id, confidence=1.0,
                status=entity.status, content={"aliases": entity.aliases},
                node_kind="canonical_entity", entity_type=entity.type,
                display_label=entity.canonical_name,
                display_type=DISPLAY_TYPES.get(entity.type, entity.type),
                review_status=entity.status,
            ))
        for mention in mentions:
            nodes.append(KnowledgeGraphNodeRead(
                id=f"mention:{mention.id}", label=mention.mention_text[:120],
                type="paper_mention", workspace_id=workspace_id, confidence=mention.confidence,
                status=mention.status, content={"start_char": mention.start_char, "end_char": mention.end_char},
                node_kind="paper_mention", mention_text=mention.mention_text,
                paper_id=mention.paper_id, canonical_entity_id=mention.canonical_entity_id,
                knowledge_item_id=mention.knowledge_item_id,
                display_label=mention.mention_text[:120],
                display_type=DISPLAY_TYPES["paper_mention"],
                evidence_count=1, paper_count=1, review_status=mention.status,
            ))

        edges: list[KnowledgeGraphEdgeRead] = []
        if item_ids:
            rel_query = select(KnowledgeRelation).where(
                KnowledgeRelation.workspace_id == workspace_id,
                KnowledgeRelation.is_deleted.is_(False),
                KnowledgeRelation.source_id.in_(item_ids),
                KnowledgeRelation.target_id.in_(item_ids),
            )
            if relation_type:
                rel_query = rel_query.where(KnowledgeRelation.relation_type == relation_type)
            for relation in self.db.execute(rel_query).scalars().all():
                edges.append(KnowledgeGraphEdgeRead(
                    id=relation.id, source=relation.source_id, target=relation.target_id,
                    relation_type=relation.relation_type, confidence=relation.confidence,
                    payload=relation.payload,
                ))
        for item in items:
            if item.paper_id and item.paper_id in paper_map:
                edges.append(KnowledgeGraphEdgeRead(
                    id=f"contains:{item.paper_id}:{item.id}", source=f"paper:{item.paper_id}",
                    target=item.id, relation_type="contains", confidence=1.0,
                ))
            if item.canonical_entity_id and any(e.id == item.canonical_entity_id for e in entities):
                edges.append(KnowledgeGraphEdgeRead(
                    id=f"canonicalizes:{item.id}:{item.canonical_entity_id}", source=item.id,
                    target=f"entity:{item.canonical_entity_id}", relation_type="canonicalizes", confidence=1.0,
                ))
        for mention in mentions:
            edges.append(KnowledgeGraphEdgeRead(
                id=f"mentioned_in:{mention.id}:{mention.paper_id}", source=f"paper:{mention.paper_id}",
                target=f"mention:{mention.id}", relation_type="mentioned_in", confidence=mention.confidence,
            ))
            edges.append(KnowledgeGraphEdgeRead(
                id=f"refers_to:{mention.id}:{mention.canonical_entity_id}", source=f"mention:{mention.id}",
                target=f"entity:{mention.canonical_entity_id}", relation_type="refers_to", confidence=mention.confidence,
            ))
            if mention.knowledge_item_id and any(item.id == mention.knowledge_item_id for item in items):
                edges.append(KnowledgeGraphEdgeRead(
                    id=f"evidences:{mention.id}:{mention.knowledge_item_id}", source=f"mention:{mention.id}",
                    target=mention.knowledge_item_id, relation_type="evidences", confidence=mention.confidence,
                ))
        structural_total = len(papers) + len(entities) + mention_total
        node_labels = {node.id: node.label for node in nodes}
        relation_counts_by_node: dict[str, int] = {node.id: 0 for node in nodes}
        enriched_edges = []
        for edge in edges:
            relation_counts_by_node[edge.source] = relation_counts_by_node.get(edge.source, 0) + 1
            relation_counts_by_node[edge.target] = relation_counts_by_node.get(edge.target, 0) + 1
            relation_group = (
                "evidence" if edge.relation_type in {"evidences", "mentioned_in", "refers_to"}
                else "structural" if edge.relation_type in {"contains", "canonicalizes"}
                else "semantic"
            )
            enriched_edges.append(edge.model_copy(update={
                "display_label": RELATION_LABELS.get(edge.relation_type, edge.relation_type),
                "source_label": node_labels.get(edge.source, edge.source),
                "target_label": node_labels.get(edge.target, edge.target),
                "relation_group": relation_group,
            }))
        max_degree = max(relation_counts_by_node.values(), default=1)
        enriched_nodes = [
            node.model_copy(update={
                "relation_count": relation_counts_by_node.get(node.id, 0),
                "importance_score": round(
                    min(1.0, node.confidence * 0.75 + relation_counts_by_node.get(node.id, 0) / max_degree * 0.25),
                    4,
                ),
            })
            for node in nodes
        ]
        return enriched_nodes, enriched_edges, structural_total, mention_total > len(mentions)

    def _graph_aggregate_counts(
        self,
        *,
        workspace_id: str,
        item_query,
        relation_type: str | None,
        include_mentions: bool,
        include_entities: bool,
        strict_paper_ids: set[str] | None = None,
    ) -> tuple[dict[str, int], dict[str, int], int, int]:
        item_subquery = item_query.subquery()
        item_ids = select(item_subquery.c.id)
        paper_ids = select(item_subquery.c.paper_id).where(
            item_subquery.c.paper_id.is_not(None)
        ).distinct()
        entity_ids = select(item_subquery.c.canonical_entity_id).where(
            item_subquery.c.canonical_entity_id.is_not(None)
        ).distinct()

        node_counts = {
            str(kind): int(count)
            for kind, count in self.db.execute(
                select(item_subquery.c.type, func.count()).group_by(item_subquery.c.type)
            ).all()
        }
        paper_count = int(self.db.execute(
            select(func.count()).select_from(Paper).where(
                Paper.workspace_id == workspace_id,
                Paper.is_deleted.is_(False),
                Paper.id.in_(paper_ids),
            )
        ).scalar() or 0)
        mention_filter = (
            PaperMention.paper_id.in_(list(strict_paper_ids))
            if strict_paper_ids is not None
            else or_(
                PaperMention.paper_id.in_(paper_ids),
                PaperMention.canonical_entity_id.in_(entity_ids),
            )
        )
        mention_count = int(self.db.execute(
            select(func.count()).select_from(PaperMention).where(
                PaperMention.workspace_id == workspace_id,
                PaperMention.is_deleted.is_(False),
                PaperMention.paper_id.in_(select(Paper.id).where(
                    Paper.workspace_id == workspace_id, Paper.is_deleted.is_(False),
                )),
                PaperMention.canonical_entity_id.in_(select(CanonicalEntity.id).where(
                    CanonicalEntity.workspace_id == workspace_id, CanonicalEntity.is_deleted.is_(False),
                )),
                mention_filter,
            )
        ).scalar() or 0) if include_mentions else 0
        all_entity_ids = entity_ids
        if include_mentions and include_entities:
            mention_entity_ids = select(PaperMention.canonical_entity_id).where(
                PaperMention.workspace_id == workspace_id,
                PaperMention.is_deleted.is_(False),
                PaperMention.paper_id.in_(select(Paper.id).where(
                    Paper.workspace_id == workspace_id, Paper.is_deleted.is_(False),
                )),
                PaperMention.canonical_entity_id.in_(select(CanonicalEntity.id).where(
                    CanonicalEntity.workspace_id == workspace_id, CanonicalEntity.is_deleted.is_(False),
                )),
                mention_filter,
            ).distinct()
            all_entity_ids = entity_ids.union(mention_entity_ids)
        entity_count = int(self.db.execute(
            select(func.count()).select_from(CanonicalEntity).where(
                CanonicalEntity.workspace_id == workspace_id,
                CanonicalEntity.is_deleted.is_(False),
                CanonicalEntity.id.in_(all_entity_ids),
            )
        ).scalar() or 0) if include_entities else 0
        node_counts.update({
            "paper": paper_count,
            "canonical_entity": entity_count,
            "paper_mention": mention_count,
        })

        relation_query = select(KnowledgeRelation.relation_type, func.count()).where(
            KnowledgeRelation.workspace_id == workspace_id,
            KnowledgeRelation.is_deleted.is_(False),
            KnowledgeRelation.source_id.in_(item_ids),
            KnowledgeRelation.target_id.in_(item_ids),
        )
        if relation_type:
            relation_query = relation_query.where(KnowledgeRelation.relation_type == relation_type)
        relation_counts = {
            str(kind): int(count)
            for kind, count in self.db.execute(
                relation_query.group_by(KnowledgeRelation.relation_type)
            ).all()
        }
        contains_count = int(self.db.execute(
            select(func.count()).select_from(item_subquery).where(item_subquery.c.paper_id.is_not(None))
        ).scalar() or 0)
        canonical_count = int(self.db.execute(
            select(func.count()).select_from(item_subquery).where(
                item_subquery.c.canonical_entity_id.is_not(None)
            )
        ).scalar() or 0)
        relation_counts["contains"] = relation_counts.get("contains", 0) + contains_count
        if include_entities:
            relation_counts["canonicalizes"] = relation_counts.get("canonicalizes", 0) + canonical_count
        if include_mentions:
            linked_mention_count = int(self.db.execute(
                select(func.count()).select_from(PaperMention).where(
                    PaperMention.workspace_id == workspace_id,
                    PaperMention.is_deleted.is_(False),
                    PaperMention.paper_id.in_(select(Paper.id).where(
                        Paper.workspace_id == workspace_id, Paper.is_deleted.is_(False),
                    )),
                    PaperMention.canonical_entity_id.in_(select(CanonicalEntity.id).where(
                        CanonicalEntity.workspace_id == workspace_id, CanonicalEntity.is_deleted.is_(False),
                    )),
                    mention_filter,
                    PaperMention.knowledge_item_id.in_(item_ids),
                )
            ).scalar() or 0)
            relation_counts.update({
                "mentioned_in": mention_count,
                "refers_to": mention_count,
                "evidences": linked_mention_count,
            })
        total_nodes = sum(node_counts.values())
        total_edges = sum(relation_counts.values())
        return node_counts, relation_counts, total_nodes, total_edges

    def _workspace_graph_counts(self, workspace_id: str) -> dict[str, int]:
        papers = int(self.db.execute(select(func.count()).select_from(Paper).where(
            Paper.workspace_id == workspace_id, Paper.is_deleted.is_(False)
        )).scalar() or 0)
        parsed_papers = int(self.db.execute(select(func.count()).select_from(Paper).where(
            Paper.workspace_id == workspace_id,
            Paper.is_deleted.is_(False),
            Paper.parse_status == "parsed",
        )).scalar() or 0)
        items = int(self.db.execute(select(func.count()).select_from(KnowledgeItem).where(
            KnowledgeItem.workspace_id == workspace_id, KnowledgeItem.is_deleted.is_(False)
        )).scalar() or 0)
        confirmed = int(self.db.execute(select(func.count()).select_from(KnowledgeItem).where(
            KnowledgeItem.workspace_id == workspace_id,
            KnowledgeItem.is_deleted.is_(False),
            KnowledgeItem.status == "human_confirmed",
        )).scalar() or 0)
        relations = int(self.db.execute(select(func.count()).select_from(KnowledgeRelation).where(
            KnowledgeRelation.workspace_id == workspace_id,
            KnowledgeRelation.is_deleted.is_(False),
        )).scalar() or 0)
        canonical_entities = int(self.db.execute(select(func.count()).select_from(CanonicalEntity).where(
            CanonicalEntity.workspace_id == workspace_id,
            CanonicalEntity.is_deleted.is_(False),
            CanonicalEntity.type.in_(WORKSPACE_ENTITY_TYPES),
        )).scalar() or 0)
        mentions = int(self.db.execute(select(func.count()).select_from(PaperMention).where(
            PaperMention.workspace_id == workspace_id,
            PaperMention.is_deleted.is_(False),
        )).scalar() or 0)
        active_item_ids = select(KnowledgeItem.id).where(
            KnowledgeItem.workspace_id == workspace_id,
            KnowledgeItem.is_deleted.is_(False),
        )
        evidence = int(self.db.execute(select(func.count()).select_from(EvidenceSpan).where(
            EvidenceSpan.workspace_id == workspace_id,
            EvidenceSpan.is_deleted.is_(False),
            EvidenceSpan.knowledge_item_id.in_(active_item_ids),
        )).scalar() or 0)
        return {
            "papers": papers,
            "parsed_papers": parsed_papers,
            "knowledge_items": items,
            "confirmed_items": confirmed,
            "relations": relations,
            "canonical_entities": canonical_entities,
            "mentions": mentions,
            "evidence_spans": evidence,
        }

    def search_graph_nodes(
        self,
        *,
        workspace_id: str,
        query_text: str,
        projection_mode: str = "all",
        limit: int = 12,
    ):
        from app.domains.knowledge.schemas import KnowledgeGraphSearchResult

        term = query_text.strip()
        if not term:
            return []
        limit = max(1, min(limit, 50))
        if projection_mode == "workspace":
            return self._search_workspace_graph_nodes(
                workspace_id=workspace_id,
                query_text=term,
                limit=limit,
            )
        results: list[KnowledgeGraphSearchResult] = []
        item_query = select(KnowledgeItem).where(
            KnowledgeItem.workspace_id == workspace_id,
            KnowledgeItem.is_deleted.is_(False),
            KnowledgeItem.canonical_name.ilike(f"%{term}%"),
        )
        mode_types = GRAPH_MODE_TYPES.get(projection_mode)
        if mode_types:
            item_query = item_query.where(KnowledgeItem.type.in_(mode_types))
        items = self.db.execute(
            item_query.order_by(KnowledgeItem.confidence.desc()).limit(limit)
        ).scalars().all()
        paper_ids = {item.paper_id for item in items if item.paper_id}
        paper_titles = {
            paper.id: paper.title for paper in self.db.execute(
                select(Paper).where(
                    Paper.id.in_(paper_ids),
                    Paper.workspace_id == workspace_id,
                    Paper.is_deleted.is_(False),
                )
            ).scalars().all()
        } if paper_ids else {}
        results.extend(KnowledgeGraphSearchResult(
            node_id=item.id,
            label=item.canonical_name,
            node_kind="knowledge",
            type=item.type,
            paper_title=paper_titles.get(item.paper_id),
            confidence=item.confidence,
        ) for item in items)

        if projection_mode in {"all", "landscape", "claims", "evidence"}:
            papers = self.db.execute(select(Paper).where(
                Paper.workspace_id == workspace_id,
                Paper.is_deleted.is_(False),
                Paper.title.ilike(f"%{term}%"),
            ).order_by(Paper.year.desc()).limit(limit)).scalars().all()
            results.extend(KnowledgeGraphSearchResult(
                node_id=f"paper:{paper.id}", label=paper.title, node_kind="paper",
                type="paper", paper_title=paper.title, confidence=1.0,
            ) for paper in papers)

        if projection_mode in {"all", "landscape", "evidence"}:
            entities = self.db.execute(select(CanonicalEntity).where(
                CanonicalEntity.workspace_id == workspace_id,
                CanonicalEntity.is_deleted.is_(False),
                CanonicalEntity.canonical_name.ilike(f"%{term}%"),
            ).limit(limit)).scalars().all()
            results.extend(KnowledgeGraphSearchResult(
                node_id=f"entity:{entity.id}", label=entity.canonical_name,
                node_kind="canonical_entity", type=entity.type, confidence=1.0,
            ) for entity in entities)
        results.sort(key=lambda item: (-item.confidence, item.label.lower()))
        return results[:limit]

    def _search_workspace_graph_nodes(
        self,
        *,
        workspace_id: str,
        query_text: str,
        limit: int,
    ):
        from app.domains.knowledge.schemas import KnowledgeGraphSearchResult

        term = query_text.lower()
        papers = list(self.db.execute(select(Paper).where(
            Paper.workspace_id == workspace_id,
            Paper.is_deleted.is_(False),
        )).scalars().all())
        paper_map = {paper.id: paper for paper in papers}
        entities = list(self.db.execute(select(CanonicalEntity).where(
            CanonicalEntity.workspace_id == workspace_id,
            CanonicalEntity.is_deleted.is_(False),
            CanonicalEntity.type.in_(WORKSPACE_ENTITY_TYPES),
        )).scalars().all())
        entity_map = {entity.id: entity for entity in entities}
        items = list(self.db.execute(select(KnowledgeItem).where(
            KnowledgeItem.workspace_id == workspace_id,
            KnowledgeItem.is_deleted.is_(False),
            KnowledgeItem.type.in_(WORKSPACE_ENTITY_TYPES),
        )).scalars().all())
        mentions = list(self.db.execute(select(PaperMention).where(
            PaperMention.workspace_id == workspace_id,
            PaperMention.is_deleted.is_(False),
        )).scalars().all())
        item_ids_by_entity: dict[str, set[str]] = defaultdict(set)
        paper_ids_by_entity: dict[str, set[str]] = defaultdict(set)
        mention_count_by_entity: dict[str, int] = defaultdict(int)
        confidence_by_entity: dict[str, list[float]] = defaultdict(list)
        for item in items:
            if item.paper_id not in paper_map or item.canonical_entity_id not in entity_map:
                continue
            item_ids_by_entity[item.canonical_entity_id].add(item.id)
            paper_ids_by_entity[item.canonical_entity_id].add(item.paper_id)
            confidence_by_entity[item.canonical_entity_id].append(item.confidence)
        for mention in mentions:
            if mention.paper_id not in paper_map or mention.canonical_entity_id not in entity_map:
                continue
            mention_count_by_entity[mention.canonical_entity_id] += 1
            paper_ids_by_entity[mention.canonical_entity_id].add(mention.paper_id)
            confidence_by_entity[mention.canonical_entity_id].append(mention.confidence)

        evidence_counts: dict[str, int] = defaultdict(int)
        item_ids = set().union(*item_ids_by_entity.values()) if item_ids_by_entity else set()
        if item_ids:
            evidence_counts.update({
                item_id: int(count)
                for item_id, count in self.db.execute(
                    select(EvidenceSpan.knowledge_item_id, func.count(EvidenceSpan.id))
                    .where(
                        EvidenceSpan.workspace_id == workspace_id,
                        EvidenceSpan.is_deleted.is_(False),
                        EvidenceSpan.knowledge_item_id.in_(list(item_ids)),
                    )
                    .group_by(EvidenceSpan.knowledge_item_id)
                ).all()
            })

        results: list[KnowledgeGraphSearchResult] = []
        for entity in entities:
            if not (
                term in entity.canonical_name.lower()
                or any(term in alias.lower() for alias in (entity.aliases or []))
            ):
                continue
            if not paper_ids_by_entity[entity.id]:
                continue
            entity_item_ids = item_ids_by_entity[entity.id]
            results.append(KnowledgeGraphSearchResult(
                node_id=f"entity:{entity.id}", label=entity.canonical_name,
                node_kind="canonical_entity", type=entity.type,
                paper_title=(
                    paper_map[sorted(paper_ids_by_entity[entity.id])[0]].title
                    if paper_ids_by_entity[entity.id] else None
                ),
                confidence=max(confidence_by_entity[entity.id] or [0.0]),
                paper_count=len(paper_ids_by_entity[entity.id]),
                mention_count=mention_count_by_entity[entity.id],
                knowledge_item_count=len(entity_item_ids),
                evidence_count=sum(evidence_counts.get(item_id, 0) for item_id in entity_item_ids),
            ))
        for paper in papers:
            if term in paper.title.lower():
                results.append(KnowledgeGraphSearchResult(
                    node_id=f"paper:{paper.id}", label=paper.title,
                    node_kind="paper", type="paper", paper_title=paper.title,
                    confidence=1.0,
                ))
        results.sort(key=lambda item: (-item.confidence, item.label.lower()))
        return results[:limit]

    @staticmethod
    def _split_graph_node_id(node_id: str) -> tuple[str, str]:
        if ":" in node_id:
            kind, raw_id = node_id.split(":", 1)
            return kind, raw_id
        return "knowledge", node_id

    # -------------------------------------------------------- evidence
    def list_evidence_for_item(
        self, item_id: str, *, workspace_id: str
    ) -> list[EvidenceSpan]:
        self._validate_uuid(item_id)
        q = select(EvidenceSpan).where(
            EvidenceSpan.knowledge_item_id == item_id,
            EvidenceSpan.workspace_id == workspace_id,
            EvidenceSpan.is_deleted.is_(False),
        )
        return list(self.db.execute(q).scalars().all())

    def review_item(
        self, *, workspace_id: str, item_id: str, payload: KnowledgeItemReview
    ) -> KnowledgeItem:
        item = self.get_item(item_id, workspace_id=workspace_id)
        if payload.action == "edit":
            if payload.canonical_name is None and payload.content is None and payload.confidence is None:
                raise KnowledgeItemReviewError("edit requires canonical_name, content, or confidence")
            if payload.canonical_name is not None:
                item.canonical_name = payload.canonical_name.strip()
            if payload.content is not None:
                item.content = payload.content
            if payload.confidence is not None:
                item.confidence = payload.confidence
            item.status = "human_confirmed"
        elif payload.action == "confirm":
            item.status = "human_confirmed"
        else:
            item.status = "rejected"
        item.reviewed_by = "user"
        item.reviewed_at = datetime.now(timezone.utc)
        item.review_note = payload.note
        item.version += 1
        self.db.commit()
        self.db.refresh(item)
        return item

    def upsert_paper_mention(
        self,
        *,
        workspace_id: str,
        paper_id: str,
        canonical_entity_id: str,
        knowledge_item_id: str | None,
        mention_text: str,
        artifact_id: str | None,
        start_char: int | None,
        end_char: int | None,
        confidence: float,
    ) -> PaperMention:
        query = select(PaperMention).where(
            PaperMention.workspace_id == workspace_id,
            PaperMention.paper_id == paper_id,
            PaperMention.canonical_entity_id == canonical_entity_id,
            PaperMention.start_char == start_char,
            PaperMention.end_char == end_char,
            PaperMention.is_deleted.is_(False),
        )
        existing = self.db.execute(query).scalar_one_or_none()
        if existing:
            return existing
        mention = PaperMention(
            id=str(uuid4()), workspace_id=workspace_id, paper_id=paper_id,
            canonical_entity_id=canonical_entity_id, knowledge_item_id=knowledge_item_id,
            mention_text=mention_text, artifact_id=artifact_id, start_char=start_char,
            end_char=end_char, confidence=confidence, status="extracted_candidate",
            is_deleted=False,
        )
        self.db.add(mention)
        self.db.flush()
        return mention

    # -------------------------------------------------------- writes (Phase 3)
    def get_or_create_canonical_entity(
        self,
        *,
        workspace_id: str,
        entity_type: str,
        canonical_name: str,
        aliases: list[str] | None = None,
    ) -> CanonicalEntity:
        normalization_key = self.normalize_entity_name(canonical_name)
        if not normalization_key:
            raise ValueError("canonical_name must contain letters or numbers")
        existing = self.db.execute(
            select(CanonicalEntity).where(
                CanonicalEntity.workspace_id == workspace_id,
                CanonicalEntity.type == entity_type,
                CanonicalEntity.normalization_key == normalization_key,
                CanonicalEntity.is_deleted.is_(False),
            )
        ).scalar_one_or_none()
        if existing:
            merged_aliases = set(existing.aliases or [])
            merged_aliases.update(aliases or [])
            if canonical_name != existing.canonical_name:
                merged_aliases.add(canonical_name)
            existing.aliases = sorted(merged_aliases)
            self.db.flush()
            return existing

        entity = CanonicalEntity(
            id=str(uuid4()),
            workspace_id=workspace_id,
            type=entity_type,
            canonical_name=canonical_name,
            normalization_key=normalization_key,
            aliases=sorted(set(aliases or [])),
            status="extracted_candidate",
            is_deleted=False,
        )
        self.db.add(entity)
        self.db.flush()
        return entity

    def upsert_item(self, payload: KnowledgeItemCreate) -> KnowledgeItem:
        """Create one paper-scoped item, idempotent within an extraction run."""
        if payload.extraction_run_id and payload.item_key:
            existing = self.db.execute(
                select(KnowledgeItem).where(
                    KnowledgeItem.extraction_run_id == payload.extraction_run_id,
                    KnowledgeItem.item_key == payload.item_key,
                    KnowledgeItem.is_deleted.is_(False),
                )
            ).scalar_one_or_none()
            if existing:
                return existing

        item = KnowledgeItem(
            id=str(uuid4()),
            workspace_id=payload.workspace_id,
            paper_id=payload.paper_id,
            canonical_entity_id=payload.canonical_entity_id,
            extraction_run_id=payload.extraction_run_id,
            item_key=payload.item_key,
            type=payload.type,
            canonical_name=payload.canonical_name,
            content=payload.content,
            source_provenance=payload.source_provenance,
            created_by=payload.created_by,
            confidence=payload.confidence,
            status=payload.status,
            version=1,
            is_deleted=False,
        )
        self.db.add(item)
        self.db.flush()
        logger.info("knowledge.created", item_id=item.id, type=item.type)
        return item

    def create_evidence_span(self, payload: EvidenceSpanCreate) -> EvidenceSpan:
        existing = self.db.execute(
            select(EvidenceSpan).where(
                EvidenceSpan.workspace_id == payload.workspace_id,
                EvidenceSpan.knowledge_item_id == payload.knowledge_item_id,
                EvidenceSpan.artifact_id == payload.artifact_id,
                EvidenceSpan.start_char == payload.start_char,
                EvidenceSpan.end_char == payload.end_char,
                EvidenceSpan.relation == payload.relation,
                EvidenceSpan.is_deleted.is_(False),
            )
        ).scalar_one_or_none()
        if existing:
            return existing

        span = EvidenceSpan(
            id=str(uuid4()),
            workspace_id=payload.workspace_id,
            knowledge_item_id=payload.knowledge_item_id,
            paper_id=payload.paper_id,
            artifact_id=payload.artifact_id,
            artifact_kind=payload.artifact_kind,
            artifact_version=payload.artifact_version,
            chunk_index=None,
            start_char=payload.start_char,
            end_char=payload.end_char,
            text=payload.text,
            relation=payload.relation,
            confidence=payload.confidence,
            is_deleted=False,
        )
        self.db.add(span)
        self.db.flush()
        return span

    def create_relation(self, payload: KnowledgeRelationCreate) -> KnowledgeRelation:
        existing = self.db.execute(
            select(KnowledgeRelation).where(
                KnowledgeRelation.workspace_id == payload.workspace_id,
                KnowledgeRelation.source_id == payload.source_id,
                KnowledgeRelation.target_id == payload.target_id,
                KnowledgeRelation.relation_type == payload.relation_type,
                KnowledgeRelation.is_deleted.is_(False),
            )
        ).scalar_one_or_none()
        if existing:
            return existing

        rel = KnowledgeRelation(
            id=str(uuid4()),
            workspace_id=payload.workspace_id,
            source_id=payload.source_id,
            target_id=payload.target_id,
            relation_type=payload.relation_type,
            confidence=payload.confidence,
            payload=payload.payload,
            is_deleted=False,
        )
        self.db.add(rel)
        self.db.flush()
        return rel

    def get_run_by_task(self, task_id: str) -> ExtractionRun | None:
        return self.db.execute(
            select(ExtractionRun).where(ExtractionRun.task_id == task_id)
        ).scalar_one_or_none()

    def get_extraction_run(self, run_id: str) -> ExtractionRun:
        try:
            UUID(str(run_id))
        except (ValueError, TypeError) as exc:
            raise ExtractionRunNotFoundError(run_id) from exc
        run = self.db.get(ExtractionRun, run_id)
        if run is None:
            raise ExtractionRunNotFoundError(run_id)
        return run

    def create_rejection(
        self, payload: ExtractionRejectionCreate
    ) -> ExtractionRejection:
        fingerprint_source = {
            "stage": payload.stage,
            "reason_code": payload.reason_code,
            "batch_index": payload.batch_index,
            "raw_payload": payload.raw_payload,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_source,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        existing = self.db.execute(
            select(ExtractionRejection).where(
                ExtractionRejection.extraction_run_id
                == payload.extraction_run_id,
                ExtractionRejection.fingerprint == fingerprint,
            )
        ).scalar_one_or_none()
        if existing:
            existing.is_deleted = False
            self.db.flush()
            return existing

        rejection = ExtractionRejection(
            id=str(uuid4()),
            workspace_id=payload.workspace_id,
            extraction_run_id=payload.extraction_run_id,
            paper_id=payload.paper_id,
            batch_index=payload.batch_index,
            rejection_kind=payload.rejection_kind,
            stage=payload.stage,
            reason_code=payload.reason_code,
            reason_detail=payload.reason_detail,
            item_type=payload.item_type,
            canonical_name=payload.canonical_name,
            raw_payload=payload.raw_payload,
            evidence_preview=payload.evidence_preview,
            fingerprint=fingerprint,
            is_deleted=False,
        )
        self.db.add(rejection)
        self.db.flush()
        return rejection

    def list_rejections(
        self,
        *,
        workspace_id: str,
        extraction_run_id: str,
        kind_filter: str | None = None,
        stage_filter: str | None = None,
        reason_code_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExtractionRejection], int]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        query = select(ExtractionRejection).where(
            ExtractionRejection.workspace_id == workspace_id,
            ExtractionRejection.extraction_run_id == extraction_run_id,
            ExtractionRejection.is_deleted.is_(False),
        )
        if kind_filter:
            query = query.where(
                ExtractionRejection.rejection_kind == kind_filter
            )
        if stage_filter:
            query = query.where(ExtractionRejection.stage == stage_filter)
        if reason_code_filter:
            query = query.where(
                ExtractionRejection.reason_code == reason_code_filter
            )
        items_query = (
            query.order_by(ExtractionRejection.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        total_query = select(func.count()).select_from(query.subquery())
        items = list(self.db.execute(items_query).scalars().all())
        total = int(self.db.execute(total_query).scalar() or 0)
        return items, total

    @staticmethod
    def normalize_entity_name(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        return re.sub(r"[\W_]+", "", normalized)

    @staticmethod
    def _validate_uuid(value: str) -> None:
        try:
            UUID(str(value))
        except (ValueError, TypeError) as e:
            raise KnowledgeItemNotFoundError(value) from e
