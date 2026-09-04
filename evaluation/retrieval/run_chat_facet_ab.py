"""确定性 Chat retrieval facet 的只读 A/B 实验。

该运行器比较当前仅 primary 的 semantic query 与实验性的 primary-plus-facet 候选并集。
它不会调用 LLM、创建 Chat message 或修改 workspace。本脚本有意不将 facet planner 接入
生产 Chat。

示例（从仓库根目录运行）：

    backend\\.venv\\Scripts\\python.exe evaluation\\retrieval\\run_chat_facet_ab.py `
      --workspace-id <workspace-id> `
      --gold evaluation\\chat\\gold\\gnn_explanations_draft_v2.json `
      --output evaluation\\retrieval\\reports\\chat_gnn_facet_ab_draft.json
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
for import_root in (REPO_ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.chat.retrieval_facets import plan_retrieval_facets  # noqa: E402
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem  # noqa: E402
from app.domains.retrieval.service import find_chunk_record, semantic_search  # noqa: E402
from evaluation.chat.gold_set import ChatQAGoldSet  # noqa: E402
from evaluation.retrieval.run_eval import resolve_many  # noqa: E402


def _audit(response: RetrievalResponse) -> dict[str, Any]:
    filters = response.filters_applied or {}
    return {
        "status": response.status,
        "diagnostic_code": response.diagnostic_code,
        "recall_count": filters.get("recall_count"),
        "returned_chunk_count": response.total,
        "latency_ms": response.latency_ms,
        "reranker_status": (
            "degraded"
            if response.diagnostic_code == "reranker_degraded"
            else "applied"
            if filters.get("reranker_applied")
            else "enabled_no_rerank"
            if filters.get("reranker_enabled")
            else "unknown"
        ),
    }


def _paper_ids(items: list[RetrievalResultItem]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item.paper_id and item.paper_id not in seen:
            seen.add(item.paper_id)
            result.append(item.paper_id)
    return result


def _item_key(item: RetrievalResultItem) -> str:
    if item.chunk_id:
        return f"chunk:{item.chunk_id}"
    if item.result_id:
        return f"result:{item.result_id}"
    return f"fallback:{item.paper_id}:{item.artifact_id}:{item.text}"


def _merge_items(responses: list[RetrievalResponse], top_k: int) -> list[RetrievalResultItem]:
    """仅以分块和论文多样性合并实验响应。"""

    best_by_chunk: dict[str, RetrievalResultItem] = {}
    for response in responses:
        for item in response.items:
            key = _item_key(item)
            previous = best_by_chunk.get(key)
            if previous is None or item.score > previous.score:
                best_by_chunk[key] = item

    ordered = sorted(
        best_by_chunk.values(),
        key=lambda item: (-item.score, item.paper_id or "", item.chunk_id or ""),
    )
    best_by_paper: dict[str, RetrievalResultItem] = {}
    paperless: list[RetrievalResultItem] = []
    for item in ordered:
        if not item.paper_id:
            paperless.append(item)
            continue
        best_by_paper.setdefault(item.paper_id, item)
    diversified = list(best_by_paper.values()) + paperless
    diversified.sort(
        key=lambda item: (-item.score, item.paper_id or "", item.chunk_id or "")
    )
    return diversified[:top_k]


def _item_snapshot(db, workspace_id: str, item: RetrievalResultItem) -> dict[str, Any]:
    """返回 provenance 和偏移，不复制检索文本。"""

    record = (
        find_chunk_record(
            workspace_id,
            item.paper_id,
            item.chunk_id,
            db=db,
        )
        if item.paper_id and item.chunk_id
        else None
    )
    record_matches = bool(
        record
        and record.workspace_id == workspace_id
        and record.paper_id == item.paper_id
    )
    return {
        "paper_id": item.paper_id,
        "artifact_id": item.artifact_id,
        "chunk_id": item.chunk_id,
        "source_scope": item.source_scope,
        "evidence_level": item.evidence_level,
        "section": (record.section if record_matches else None) or item.section,
        "chunk_index": record.chunk_index if record_matches else None,
        "start_char": record.start_char if record_matches else None,
        "end_char": record.end_char if record_matches else None,
        "chunk_record_resolved": record_matches,
        "score": round(item.score, 6),
    }


def _coverage(required_ids: set[str], items: list[RetrievalResultItem]) -> float | None:
    if not required_ids:
        return None
    return len(required_ids & set(_paper_ids(items))) / len(required_ids)


def _resolve_required_ids(db, workspace_id: str, refs: list[str]) -> set[str]:
    resolved = resolve_many(db, workspace_id, refs)
    return {paper_id for paper_id in resolved.values() if paper_id}


def run_experiment(
    *,
    workspace_id: str,
    gold: ChatQAGoldSet,
    top_k: int,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        rows: list[dict[str, Any]] = []
        for question in gold.questions:
            required_ids = _resolve_required_ids(db, workspace_id, question.required_paper_refs)
            primary = semantic_search(
                workspace_id,
                question.question,
                top_k=top_k,
                use_reranker=True,
                diversify_by_paper=True,
            )
            facets = plan_retrieval_facets(question.question)
            facet_responses = [
                semantic_search(
                    workspace_id,
                    facet.query,
                    top_k=top_k,
                    use_reranker=True,
                    diversify_by_paper=True,
                )
                for facet in facets
            ]
            merged = _merge_items([primary, *facet_responses], top_k)
            all_responses = [primary, *facet_responses]
            faceted_status = (
                "failed"
                if any(response.status == "failed" for response in all_responses)
                else "degraded"
                if any(response.status == "degraded" for response in all_responses)
                else "succeeded"
            )
            rows.append(
                {
                    "query_id": question.query_id,
                    "question": question.question,
                    "expected_verdict": question.expected_verdict,
                    "required_paper_refs": question.required_paper_refs,
                    "facet_names": [facet.name for facet in facets],
                    "facet_section_hints": {
                        facet.name: list(facet.section_hints) for facet in facets
                    },
                    "section_hints_applied": False,
                    "primary": {
                        "audit": _audit(primary),
                        "paper_ids": _paper_ids(primary.items),
                        "paper_count": len(_paper_ids(primary.items)),
                        "paper_titles": [item.paper_title for item in primary.items if item.paper_title],
                        "items": [_item_snapshot(db, workspace_id, item) for item in primary.items],
                        "required_paper_coverage": (
                            _coverage(required_ids, primary.items)
                            if primary.status != "failed"
                            else None
                        ),
                    },
                    "faceted": {
                        "audit": {
                            "status": faceted_status,
                            "query_count": len(facet_responses),
                            "recall_count": sum(
                                (response.filters_applied or {}).get("recall_count", 0)
                                for response in facet_responses
                            ),
                            "returned_chunk_count": len(merged),
                            "latency_ms": round(
                                primary.latency_ms
                                + sum(response.latency_ms for response in facet_responses),
                                2,
                            ),
                            "reranker_statuses": [
                                _audit(response)["reranker_status"] for response in facet_responses
                            ],
                            "response_audits": [_audit(response) for response in facet_responses],
                            "merge_policy": "chunk_dedupe_then_paper_dedupe",
                            "section_hints_mode": "diagnostic_only",
                        },
                        "paper_ids": _paper_ids(merged),
                        "paper_count": len(_paper_ids(merged)),
                        "paper_titles": [item.paper_title for item in merged if item.paper_title],
                        "items": [_item_snapshot(db, workspace_id, item) for item in merged],
                        "required_paper_coverage": (
                            _coverage(required_ids, merged)
                            if faceted_status != "failed"
                            else None
                        ),
                    },
                }
            )

        comparable = [
            row
            for row in rows
            if row["primary"]["required_paper_coverage"] is not None
            and row["faceted"]["required_paper_coverage"] is not None
        ]
        improved = [
            row
            for row in comparable
            if row["faceted"]["required_paper_coverage"]
            > row["primary"]["required_paper_coverage"]
        ]
        regressed = [
            row
            for row in comparable
            if row["faceted"]["required_paper_coverage"]
            < row["primary"]["required_paper_coverage"]
        ]
        return {
            "schema_version": "1.0",
            "experiment": "chat_facet_ab_draft",
            "annotation_status": gold.annotation_status,
            "workspace_id": workspace_id,
            "gold_case_id": gold.case_id,
            "corpus_version": gold.corpus_version,
            "top_k": top_k,
            "production_enabled": False,
            "llm_called": False,
            "workspace_mutated": False,
            "summary": {
                "questions": len(rows),
                "facet_questions": sum(bool(row["facet_names"]) for row in rows),
                "coverage_comparable_questions": len(comparable),
                "coverage_improved_questions": len(improved),
                "coverage_regressed_questions": len(regressed),
                "primary_status_counts": dict(
                    sorted(Counter(row["primary"]["audit"]["status"] for row in rows).items())
                ),
                "faceted_status_counts": dict(
                    sorted(Counter(row["faceted"]["audit"]["status"] for row in rows).items())
                ),
                "primary_diagnostic_counts": dict(
                    sorted(
                        Counter(
                            row["primary"]["audit"]["diagnostic_code"]
                            for row in rows
                            if row["primary"]["audit"]["diagnostic_code"]
                        ).items()
                    )
                ),
                "experiment_usable": bool(comparable),
                "primary_mean_required_paper_coverage": (
                    sum(row["primary"]["required_paper_coverage"] for row in comparable)
                    / len(comparable)
                    if comparable
                    else None
                ),
                "faceted_mean_required_paper_coverage": (
                    sum(row["faceted"]["required_paper_coverage"] for row in comparable)
                    / len(comparable)
                    if comparable
                    else None
                ),
            },
            "items": rows,
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=6)
    args = parser.parse_args()

    gold = ChatQAGoldSet.model_validate_json(args.gold.read_text(encoding="utf-8-sig"))
    report = run_experiment(
        workspace_id=args.workspace_id,
        gold=gold,
        top_k=args.top_k,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
