"""确认 research-opportunity 证据引用的知识项。

演示语料有 881 个抽取知识项，尚无人工确认项。“关键”知识是支撑现有研究机会的小集合
（通过 EvidenceSpan -> OpportunityEvidence -> version -> opportunity 到达的项）。
确认这些项（HITL 演示准备，已获用户授权）会将就绪度指标从“0 条已确认”变为“N 条已确认”，
其余项仍如实保持 pending。

默认是只读试运行；传入 ``--apply`` 才会实际确认。

用法（从 backend/ 目录运行）：
    .venv/Scripts/python.exe scripts/confirm_key_knowledge.py --workspace-id <wid> [--apply]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.discover.models import (  # noqa: E402
    OpportunityEvidence,
    OpportunityVersion,
    ResearchOpportunity,
)
from app.domains.knowledge.models import EvidenceSpan, KnowledgeItem  # noqa: E402
from app.domains.knowledge.schemas import KnowledgeItemReview  # noqa: E402
from app.domains.knowledge.service import KnowledgeService  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

NOTE = "demo 关键证据确认（被研究机会引用）"


def key_item_ids(db, workspace_id: str) -> list[str]:
    """至少支撑一个 opportunity 证据的 KnowledgeItem。"""
    rows = (
        db.query(KnowledgeItem.id)
        .join(EvidenceSpan, EvidenceSpan.knowledge_item_id == KnowledgeItem.id)
        .join(OpportunityEvidence, OpportunityEvidence.evidence_span_id == EvidenceSpan.id)
        .join(OpportunityVersion, OpportunityVersion.id == OpportunityEvidence.opportunity_version_id)
        .join(ResearchOpportunity, ResearchOpportunity.id == OpportunityVersion.opportunity_id)
        .filter(
            ResearchOpportunity.workspace_id == workspace_id,
            ResearchOpportunity.is_deleted.is_(False),
            KnowledgeItem.is_deleted.is_(False),
        )
        .distinct()
        .all()
    )
    return [row[0] for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually confirm (default: dry run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        ids = key_item_ids(db, args.workspace_id)
        items = (
            db.query(KnowledgeItem)
            .filter(KnowledgeItem.id.in_(ids), KnowledgeItem.is_deleted.is_(False))
            .all()
        )
# 确定性排序：type、canonical_name
        items.sort(key=lambda i: (i.type or "", i.canonical_name or ""))

        already = sum(1 for i in items if i.status == "human_confirmed")
        print(f"key knowledge items referenced by opportunity evidence: {len(ids)} "
              f"(already confirmed: {already})")
        for i in items:
            paper = db.get(Paper, i.paper_id)
            title = paper.title[:34] if paper else "?"
            print(f"  [{i.status:18}] {i.type:10} {i.canonical_name[:40]:40} conf={i.confidence:.2f} :: {title}")
            content = i.content or {}
            text = str(content.get("statement") or content.get("description") or "")
            if text:
                print(f"        {text[:90]}")

        confirmed_before = db.query(func.count()).select_from(KnowledgeItem).filter(
            KnowledgeItem.workspace_id == args.workspace_id,
            KnowledgeItem.status == "human_confirmed",
            KnowledgeItem.is_deleted.is_(False),
        ).scalar()
        print(f"\nconfirmed before: {confirmed_before}")

        if not args.apply:
            print("\n(dry run — pass --apply to confirm these items)")
            return

        ks = KnowledgeService(db)
        confirmed = 0
        for item in items:
            if item.status == "human_confirmed":
                continue
            ks.review_item(
                workspace_id=args.workspace_id,
                item_id=item.id,
                payload=KnowledgeItemReview(action="confirm", note=NOTE),
            )
            confirmed += 1
        confirmed_after = db.query(func.count()).select_from(KnowledgeItem).filter(
            KnowledgeItem.workspace_id == args.workspace_id,
            KnowledgeItem.status == "human_confirmed",
            KnowledgeItem.is_deleted.is_(False),
        ).scalar()
        print(f"confirmed now: {confirmed_after} (+{confirmed})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
