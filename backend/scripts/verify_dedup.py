"""Verify P0 exact-dedup by re-extracting a paper and comparing run stats.

The P0 dedup (``extraction/dedup.py``) only affects NEW extractions — it
does not retroactively change existing knowledge_items. This script:

  1. snapshots the paper's most recent extraction run (item counts +
     claim/limitation duplicate groups);
  2. creates a fresh Task + ExtractionRun and runs the extraction pipeline
     synchronously in-process (same ``_run_extract`` / ``_write_extraction``
     the Celery worker calls, but without needing the worker);
  3. snapshots the NEW run's duplicate groups;
  4. prints a before/after comparison and a verdict.

Usage (from backend/):

    .venv/Scripts/python.exe scripts/verify_dedup.py \
        --workspace-id 533c89cd-625f-45e7-8a44-cc737244273c \
        --paper-id 8eb9634d-36fc-4a0a-b4c8-d61659740330

Requires a configured REMOTE API key (the extraction makes a real LLM
call). The old items are left untouched; a NEW extraction_run is created
alongside them for comparison.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.knowledge.models import (
    ExtractionRejection,
    KnowledgeItem,
    KnowledgeRelation,
)  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.task.models import Task  # noqa: E402
from app.domains.task.schemas import TaskCreate  # noqa: E402
from app.domains.task.service import TaskService  # noqa: E402
from app.workers.tasks.extract_knowledge import _run_extract  # noqa: E402
from app.workers.tasks.extraction.dedup import content_signature  # noqa: E402


def content_text(item: dict[str, Any]) -> str:
    content = item.get("content") or {}
    return str(content.get("statement") or content.get("description") or "")


def run_stats(db, run_id: str) -> dict[str, Any]:
    """Count claim/limitation items and detect duplicate groups in a run."""
    rows = db.query(KnowledgeItem).filter(
        KnowledgeItem.extraction_run_id == run_id,
        KnowledgeItem.type.in_(["claim", "limitation"]),
        KnowledgeItem.is_deleted.is_(False),
    ).all()

    total = len(rows)
    same_span_collisions = 0  # same span, claim vs limitation
    exact_dups = 0  # same (type, span, content sig)
    near_dups = 0  # same normalized content, different span (diagnostic)

    span_to_types: dict[tuple[str, int, int], set[str]] = {}
    by_span_sig: dict[tuple[str, int, int, str], list] = {}
    by_content: dict[str, list] = {}
    samples: list[str] = []

    for row in rows:
        sp = row.source_provenance or {}
        start, end = sp.get("start_char"), sp.get("end_char")
        if start is None or end is None:
            continue
        span_key = (row.paper_id or "", int(start), int(end))
        sig = content_signature(row.content)
        text = content_text({"content": row.content}).strip().casefold()

        span_to_types.setdefault(span_key, set()).add(row.type)

        group = by_span_sig.setdefault((row.type, span_key[0], span_key[1], span_key[2], sig), [])
        group.append(row)
        if len(group) == 2:
            exact_dups += 1
            samples.append(f"EXACT  type={row.type} span={start}-{end} name={row.canonical_name}")

        if text:
            cg = by_content.setdefault(text, [])
            if any(prev.span_key != span_key for prev in cg):
                near_dups += 1
            cg.append(type("R", (), {"span_key": span_key})())

    for span_key, types in span_to_types.items():
        if len(types) > 1 and types.issubset({"claim", "limitation"}):
            same_span_collisions += 1
            samples.append(f"CROSS  span={span_key[1]}-{span_key[2]} types={sorted(types)}")

    return {
        "total": total,
        "same_span_collisions": same_span_collisions,
        "exact_dups": exact_dups,
        "near_dups": near_dups,
        "samples": samples[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=600)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        paper = db.query(Paper).filter(
            Paper.id == args.paper_id,
            Paper.workspace_id == args.workspace_id,
            Paper.is_deleted.is_(False),
        ).first()
        if paper is None:
            print(f"paper not found in workspace: {args.paper_id}")
            return 1
        if not paper.parsed_markdown_artifact_id:
            print("paper has no parsed_markdown — upload + parse first")
            return 1

        # Snapshot OLD run.
        # Snapshot OLD run: pick the most recent run that actually has items
        # (a just-failed run with 0 items isn't a useful baseline).
        old_run_id = None
        for task in (
            db.query(Task)
            .filter(
                Task.workspace_id == args.workspace_id,
                Task.task_type == "extract_knowledge",
                Task.is_deleted.is_(False),
            )
            .order_by(Task.created_at.desc())
            .all()
        ):
            candidate = (task.payload or {}).get("extraction_run_id")
            if candidate and run_stats(db, candidate)["total"] > 0:
                old_run_id = candidate
                break
        old_stats = run_stats(db, old_run_id) if old_run_id else {"total": 0, "same_span_collisions": 0, "exact_dups": 0, "near_dups": 0, "samples": []}

        print(f"== paper {paper.title[:60]} ==")
        print(f"old run: {old_run_id}")
        print(f"  old claim/limitation: total={old_stats['total']} "
              f"same_span_collisions={old_stats['same_span_collisions']} "
              f"exact_dups={old_stats['exact_dups']} near_dups={old_stats['near_dups']}")

        # Trigger NEW extraction synchronously.
        print("\n-- triggering fresh extraction (real LLM call, may take a while) --")
        task = TaskService(db).create(
            TaskCreate(
                workspace_id=args.workspace_id,
                task_type="extract_knowledge",
                payload={"paper_id": args.paper_id},
            )
        )
        start = time.perf_counter()
        result = _run_extract(db, task.id)
        elapsed = time.perf_counter() - start
        db.refresh(task)
        new_run_id = (task.payload or {}).get("extraction_run_id")

        if result.get("status") != "succeeded":
            print(f"extraction FAILED: {result.get('error') or result.get('status')}")
            print("  (check REMOTE_API_KEY / network)")
            return 1

        new_stats = run_stats(db, new_run_id) if new_run_id else {"total": 0, "same_span_collisions": 0, "exact_dups": 0, "near_dups": 0, "samples": []}

        print(f"new run: {new_run_id}  (took {elapsed:.1f}s)")
        print(f"  new claim/limitation: total={new_stats['total']} "
              f"same_span_collisions={new_stats['same_span_collisions']} "
              f"exact_dups={new_stats['exact_dups']} near_dups={new_stats['near_dups']}")

        # Verdict
        dedup_fired = (
            db.query(ExtractionRejection)
            .filter(
                ExtractionRejection.extraction_run_id == new_run_id,
                ExtractionRejection.stage == "dedup_exact",
            )
            .count()
        )

        print("\n== comparison ==")
        improved = (
            new_stats["same_span_collisions"] == 0
            and new_stats["exact_dups"] == 0
        )
        print(f"  same_span_collisions: {old_stats['same_span_collisions']} -> {new_stats['same_span_collisions']}")
        print(f"  exact_dups:           {old_stats['exact_dups']} -> {new_stats['exact_dups']}")
        print(f"  near_dups:            {old_stats['near_dups']} -> {new_stats['near_dups']}")
        print(f"  dedup_exact rejections in NEW run: {dedup_fired}")
        if old_stats["samples"]:
            print("\n  old-run duplicates:")
            for s in old_stats["samples"]:
                print(f"    {s}")
        if new_stats["samples"]:
            print("\n  NEW-run remaining duplicates:")
            for s in new_stats["samples"]:
                print(f"    {s}")

        print(f"\nverdict: {'PASS — dedup eliminated exact/span collisions' if improved else 'FAIL — dedup did not fully collapse duplicates'}")
        return 0 if improved else 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
