"""Build the bounded, auditable input used by the gap extractor.

Knowledge extraction remains the general-purpose extraction layer.  This
module projects the useful, paper-local subset of that layer into the
specialized methods/problems prompt.  The Markdown path is kept as an
explicit compatibility fallback for papers that predate knowledge
extraction or whose run produced no usable items.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.gap.markdown import compact_markdown
from app.domains.knowledge.models import (
    EvidenceSpan,
    ExtractionRun,
    KnowledgeItem,
    KnowledgeRelation,
)
from app.domains.paper.models import Paper

KNOWLEDGE_CONTEXT_MODE = "knowledge_context_v1"
LEGACY_CONTEXT_MODE = "core_markdown_legacy_v1"
_KNOWLEDGE_ITEM_TYPES = ("method", "task", "claim", "limitation")
_IGNORED_ITEM_STATUSES = ("rejected", "invalidated", "deprecated")
_ITEM_TYPE_ORDER = {name: index for index, name in enumerate(_KNOWLEDGE_ITEM_TYPES)}


class GapKnowledgeExtractionPendingError(Exception):
    """Raised when a paper is not ready for dependent gap extraction."""


@dataclass(frozen=True)
class GapContextIdentity:
    """Identity used for idempotency and stale-annotation checks."""

    input_mode: str
    knowledge_extraction_run_id: str | None
    knowledge_context_sha256: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class GapExtractionContext:
    """Bounded model input plus the source lineage behind it."""

    text: str
    input_mode: str
    knowledge_extraction_run_id: str | None
    knowledge_context_sha256: str | None
    knowledge_item_ids: list[str]
    evidence_span_ids: list[str]
    context_char_count: int
    fallback_reason: str | None = None


def _latest_successful_run(db: Session, paper: Paper) -> ExtractionRun | None:
    return db.execute(
        select(ExtractionRun)
        .where(
            ExtractionRun.workspace_id == paper.workspace_id,
            ExtractionRun.paper_id == paper.id,
            ExtractionRun.status == "succeeded",
            ExtractionRun.artifact_id == paper.parsed_markdown_artifact_id,
        )
        .order_by(ExtractionRun.finished_at.desc(), ExtractionRun.updated_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _knowledge_items(db: Session, paper: Paper, run: ExtractionRun) -> list[KnowledgeItem]:
    rows = list(
        db.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.workspace_id == paper.workspace_id,
                KnowledgeItem.paper_id == paper.id,
                KnowledgeItem.extraction_run_id == run.id,
                KnowledgeItem.type.in_(_KNOWLEDGE_ITEM_TYPES),
                KnowledgeItem.status.not_in(_IGNORED_ITEM_STATUSES),
                KnowledgeItem.is_deleted.is_(False),
            )
        ).scalars()
    )
    if not rows:
        return []
    item_ids = [item.id for item in rows]
    locatable_item_ids = {
        item_id
        for item_id in db.execute(
            select(EvidenceSpan.knowledge_item_id).where(
                EvidenceSpan.workspace_id == paper.workspace_id,
                EvidenceSpan.paper_id == paper.id,
                EvidenceSpan.knowledge_item_id.in_(item_ids),
                EvidenceSpan.is_deleted.is_(False),
                or_(
                    EvidenceSpan.text.is_not(None),
                    EvidenceSpan.start_char.is_not(None),
                    EvidenceSpan.end_char.is_not(None),
                ),
            )
        ).scalars()
    }
    return sorted(
        [item for item in rows if item.id in locatable_item_ids],
        key=lambda item: (
            _ITEM_TYPE_ORDER.get(item.type, len(_ITEM_TYPE_ORDER)),
            -(item.confidence or 0.0),
            item.created_at,
            item.id,
        ),
    )


def get_gap_context_identity(db: Session, paper: Paper) -> GapContextIdentity:
    """Return the cheap lineage identity used before queueing a task."""
    if settings.gap_extraction_context_mode == LEGACY_CONTEXT_MODE:
        return GapContextIdentity(LEGACY_CONTEXT_MODE, None)

    run = _latest_successful_run(db, paper)
    if run is not None and _knowledge_items(db, paper, run):
        return GapContextIdentity(KNOWLEDGE_CONTEXT_MODE, run.id)
    if run is not None and settings.gap_extraction_allow_legacy_markdown_fallback:
        return GapContextIdentity(
            LEGACY_CONTEXT_MODE,
            run.id,
            fallback_reason="knowledge_items_unavailable",
        )
    return GapContextIdentity(
        LEGACY_CONTEXT_MODE,
        None,
        fallback_reason="knowledge_extraction_unavailable",
    )


def _clean_text(value: str | None) -> str:
    return (value or "").replace("\x00", "").strip()


def _json_text(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return _clean_text(str(value))


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    if max_chars <= 80:
        return text[:max_chars]
    head = max_chars * 2 // 3
    tail = max_chars - head - 40
    return text[:head] + "\n...[context truncated]...\n" + text[-tail:]


def _build_knowledge_text(
    paper: Paper,
    items: list[KnowledgeItem],
    evidence_by_item: dict[str, list[EvidenceSpan]],
    relations: list[KnowledgeRelation],
) -> str:
    lines = [
        "研究空白分析输入版本: knowledge_context_v1",
        f"论文标题: {_clean_text(paper.title)}",
        f"作者: {', '.join(_clean_text(item) for item in (paper.authors or []))}",
        f"年份: {paper.year or ''}",
        "以下内容来自同一篇论文的通用知识抽取结果，不是未经筛选的全文。",
        "",
    ]
    for item in items:
        lines.extend(
            [
                f"[KnowledgeItem:{item.id}] 类型={item.type} 置信度={item.confidence:.4f}",
                f"名称: {_clean_text(item.canonical_name)}",
                f"内容: {_json_text(item.content)}",
            ]
        )
        spans = evidence_by_item.get(item.id, [])
        if spans:
            lines.append("证据片段:")
            for span in spans:
                text = _clean_text(span.text)
                if text:
                    lines.append(
                        f"- [EvidenceSpan:{span.id}] 关系={span.relation} 文本={text}"
                    )
        lines.append("")

    if relations:
        lines.append("知识关系:")
        for relation in relations:
            lines.append(
                f"- [KnowledgeRelation:{relation.id}] {relation.source_id}"
                f" --{relation.relation_type}--> {relation.target_id}"
            )
    return "\n".join(lines).strip()


def build_gap_context(db: Session, paper: Paper, raw_markdown: str) -> GapExtractionContext:
    """Build the configured context and retain enough IDs to audit it later."""
    if settings.gap_extraction_context_mode == LEGACY_CONTEXT_MODE:
        legacy = compact_markdown(raw_markdown)
        return GapExtractionContext(
            text=legacy,
            input_mode=LEGACY_CONTEXT_MODE,
            knowledge_extraction_run_id=None,
            knowledge_context_sha256=None,
            knowledge_item_ids=[],
            evidence_span_ids=[],
            context_char_count=len(legacy),
        )

    run = _latest_successful_run(db, paper)
    items = _knowledge_items(db, paper, run) if run is not None else []
    if run is not None and items:
        item_ids = [item.id for item in items]
        spans = list(
            db.execute(
                select(EvidenceSpan).where(
                    EvidenceSpan.workspace_id == paper.workspace_id,
                    EvidenceSpan.paper_id == paper.id,
                    EvidenceSpan.knowledge_item_id.in_(item_ids),
                    EvidenceSpan.is_deleted.is_(False),
                )
            ).scalars()
        )
        evidence_by_item: dict[str, list[EvidenceSpan]] = {}
        for span in spans:
            evidence_by_item.setdefault(span.knowledge_item_id, []).append(span)
        for item_id in evidence_by_item:
            evidence_by_item[item_id].sort(
                key=lambda span: (
                    -(span.confidence or 0.0),
                    span.start_char if span.start_char is not None else 2**31,
                    span.id,
                )
            )
        relations = list(
            db.execute(
                select(KnowledgeRelation).where(
                    KnowledgeRelation.workspace_id == paper.workspace_id,
                    KnowledgeRelation.is_deleted.is_(False),
                    or_(
                        KnowledgeRelation.source_id.in_(item_ids),
                        KnowledgeRelation.target_id.in_(item_ids),
                    ),
                )
            ).scalars()
        )
        relations.sort(key=lambda relation: relation.id)
        text = _truncate(
            _build_knowledge_text(paper, items, evidence_by_item, relations),
            settings.gap_extraction_context_max_chars,
        )
        return GapExtractionContext(
            text=text,
            input_mode=KNOWLEDGE_CONTEXT_MODE,
            knowledge_extraction_run_id=run.id,
            knowledge_context_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            knowledge_item_ids=item_ids,
            evidence_span_ids=[span.id for item_id in item_ids for span in evidence_by_item.get(item_id, [])],
            context_char_count=len(text),
        )

    if not settings.gap_extraction_allow_legacy_markdown_fallback:
        reason = "knowledge_extraction_unavailable" if run is None else "knowledge_items_unavailable"
        return GapExtractionContext(
            text="",
            input_mode=LEGACY_CONTEXT_MODE,
            knowledge_extraction_run_id=run.id if run is not None else None,
            knowledge_context_sha256=None,
            knowledge_item_ids=[],
            evidence_span_ids=[],
            context_char_count=0,
            fallback_reason=reason,
        )

    legacy = compact_markdown(raw_markdown)
    return GapExtractionContext(
        text=legacy,
        input_mode=LEGACY_CONTEXT_MODE,
        knowledge_extraction_run_id=run.id if run is not None else None,
        knowledge_context_sha256=None,
        knowledge_item_ids=[],
        evidence_span_ids=[],
        context_char_count=len(legacy),
        fallback_reason=("knowledge_extraction_unavailable" if run is None else "knowledge_items_unavailable"),
    )
