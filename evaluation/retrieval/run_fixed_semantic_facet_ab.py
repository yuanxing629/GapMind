"""在固定 Retrieval Gold Set 上对 Chat facet 进行只读 A/B 评测。

现有 Retrieval Gate manifest 包含 semantic-search query，而不是 Chat QA question。本运行器
对这些 semantic query 应用同一个确定性的 facet 候选并集实验，不调用 LLM、不创建 Chat
message，也不修改 workspace。由于 facet 是 Chat query-planning 实验，只评估 semantic-search
区块；similar-work 和 counter-evidence 区块保持不变。

示例（从仓库根目录运行）：

    backend\\.venv\\Scripts\\python.exe evaluation\\retrieval\\run_fixed_semantic_facet_ab.py `
      --workspace-id <workspace-id> `
      --gold evaluation\\retrieval\\gold\\minimal_gnn_v1.json `
      --output evaluation\\retrieval\\reports\\minimal_gnn_facet_ab.json
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
from app.domains.retrieval.service import semantic_search  # noqa: E402
from evaluation.retrieval.gold_set import GoldSet  # noqa: E402
from evaluation.retrieval.metrics import (  # noqa: E402
    mrr_at_k,
    paper_diversity,
    recall_at_k,
    workspace_leakage,
)
from evaluation.retrieval.run_chat_facet_ab import (  # noqa: E402
    _audit,
    _merge_items,
    _paper_ids,
)
from evaluation.retrieval.run_eval import (  # noqa: E402
    _paper_workspace_ids,
    resolve_paper_ref,
)


def _status(responses: list[RetrievalResponse], primary: RetrievalResponse) -> str:
    all_responses = [primary, *responses]
    if any(response.status == "failed" for response in all_responses):
        return "failed"
    if any(response.status == "degraded" for response in all_responses):
        return "degraded"
    return "succeeded"


def _metrics(
    db,
    workspace_id: str,
    response: RetrievalResponse,
    target_paper_id: str,
    top_k: int,
) -> dict[str, Any]:
    """为一次检索响应返回 fail-closed 的论文级指标。"""

    if response.status == "failed":
        return {
            f"recall@{top_k}": None,
            f"mrr@{top_k}": None,
            "paper_diversity": None,
            "workspace_leakage": None,
        }
    paper_ids = _paper_ids(response.items)
    return {
        f"recall@{top_k}": round(recall_at_k({target_paper_id}, paper_ids, top_k), 4),
        f"mrr@{top_k}": round(mrr_at_k({target_paper_id}, paper_ids, top_k), 4),
        "paper_diversity": round(paper_diversity(paper_ids, top_k), 4),
        "workspace_leakage": round(
            workspace_leakage(
                _paper_workspace_ids(db, response.items, workspace_id),
                workspace_id,
            ),
            4,
        ),
    }


def _side_report(
    db,
    workspace_id: str,
    response: RetrievalResponse,
    target_paper_id: str,
    top_k: int,
) -> dict[str, Any]:
    paper_ids = _paper_ids(response.items)
    return {
        "audit": _audit(response),
        "paper_ids": paper_ids,
        "paper_count": len(paper_ids),
        "metrics": _metrics(db, workspace_id, response, target_paper_id, top_k),
    }


def run_experiment(*, workspace_id: str, gold: GoldSet, top_k: int) -> dict[str, Any]:
    db = SessionLocal()
    try:
        rows: list[dict[str, Any]] = []
        for query in gold.semantic_search:
            target = resolve_paper_ref(db, workspace_id, query.target_paper_ref)
            if target is None:
                rows.append(
                    {
                        "query_id": query.query_id,
                        "query": query.query,
                        "target_paper_ref": query.target_paper_ref,
                        "error": f"unresolved target_paper_ref: {query.target_paper_ref}",
                    }
                )
                continue

            primary = semantic_search(
                workspace_id,
                query.query,
                top_k=top_k,
                use_reranker=True,
                diversify_by_paper=True,
            )
            facets = plan_retrieval_facets(query.query)
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
            response_set = [primary, *facet_responses]
            recall_count = sum(
                (response.filters_applied or {}).get("recall_count", 0)
                for response in response_set
            )
            reranker_applied = all(
                (response.filters_applied or {}).get("reranker_applied", False)
                for response in response_set
                if response.status != "failed"
            )
            faceted = RetrievalResponse(
                request_id=primary.request_id,
                workspace_id=workspace_id,
                query=query.query,
                purpose="semantic",
                status=_status(facet_responses, primary),
                items=merged,
                total=len(merged),
                latency_ms=round(
                    primary.latency_ms + sum(response.latency_ms for response in facet_responses),
                    2,
                ),
                filters_applied={
                    "query_count": 1 + len(facet_responses),
                    "primary": primary.filters_applied,
                    "recall_count": recall_count,
                    "reranker_applied": reranker_applied,
                    "reranker_enabled": any(
                        (response.filters_applied or {}).get("reranker_enabled", False)
                        for response in response_set
                    ),
                },
                diagnostic_code=next(
                    (
                        response.diagnostic_code
                        for response in [primary, *facet_responses]
                        if response.diagnostic_code
                    ),
                    None,
                ),
            )
            rows.append(
                {
                    "query_id": query.query_id,
                    "query": query.query,
                    "target_paper_ref": query.target_paper_ref,
                    "target_paper_id": target.id,
                    "facet_names": [facet.name for facet in facets],
                    "facet_queries": [
                        {
                            "name": facet.name,
                            "query": facet.query,
                            "matched_triggers": list(facet.matched_triggers),
                            "section_hints": list(facet.section_hints),
                        }
                        for facet in facets
                    ],
                    "primary": _side_report(
                        db, workspace_id, primary, target.id, top_k
                    ),
                    "faceted": _side_report(
                        db, workspace_id, faceted, target.id, top_k
                    ),
                }
            )

        comparable = [
            row
            for row in rows
            if "primary" in row
            and row["primary"]["metrics"][f"recall@{top_k}"] is not None
            and row["faceted"]["metrics"][f"recall@{top_k}"] is not None
        ]
        facet_comparable = [row for row in comparable if row["facet_names"]]
        improved = [
            row
            for row in facet_comparable
            if row["faceted"]["metrics"][f"recall@{top_k}"]
            > row["primary"]["metrics"][f"recall@{top_k}"]
        ]
        regressed = [
            row
            for row in facet_comparable
            if row["faceted"]["metrics"][f"recall@{top_k}"]
            < row["primary"]["metrics"][f"recall@{top_k}"]
        ]

        def mean(rows_to_average: list[dict[str, Any]], side: str, metric: str) -> float | None:
            values = [row[side]["metrics"][metric] for row in rows_to_average]
            values = [value for value in values if isinstance(value, (int, float))]
            return round(sum(values) / len(values), 4) if values else None

        return {
            "schema_version": "1.0",
            "experiment": "fixed_retrieval_semantic_facet_ab",
            "gold_case_id": gold.case_id,
            "gold_schema_version": gold.schema_version,
            "corpus_version": gold.corpus_version,
            "workspace_id": workspace_id,
            "top_k": top_k,
            "production_enabled": False,
            "llm_called": False,
            "workspace_mutated": False,
            "evaluated_blocks": ["semantic_search"],
            "not_evaluated_blocks": {
                "similar_work": len(gold.similar_work),
                "counter_evidence": len(gold.counter_evidence),
            },
            "summary": {
                "semantic_queries": len(rows),
                "unresolved_queries": sum("error" in row for row in rows),
                "facet_questions": sum(bool(row.get("facet_names")) for row in rows),
                "comparable_queries": len(comparable),
                "facet_comparable_queries": len(facet_comparable),
                "coverage_improved_queries": len(improved),
                "coverage_regressed_queries": len(regressed),
                "primary_status_counts": dict(
                    sorted(
                        Counter(
                            row["primary"]["audit"]["status"]
                            for row in rows
                            if "primary" in row
                        ).items()
                    )
                ),
                "faceted_status_counts": dict(
                    sorted(
                        Counter(
                            row["faceted"]["audit"]["status"]
                            for row in rows
                            if "faceted" in row
                        ).items()
                    )
                ),
                f"primary_mean_recall@{top_k}": mean(
                    comparable, "primary", f"recall@{top_k}"
                ),
                f"faceted_mean_recall@{top_k}": mean(
                    comparable, "faceted", f"recall@{top_k}"
                ),
                f"primary_mean_mrr@{top_k}": mean(comparable, "primary", f"mrr@{top_k}"),
                f"faceted_mean_mrr@{top_k}": mean(comparable, "faceted", f"mrr@{top_k}"),
                "primary_max_workspace_leakage": max(
                    (
                        row["primary"]["metrics"]["workspace_leakage"]
                        for row in comparable
                        if row["primary"]["metrics"]["workspace_leakage"] is not None
                    ),
                    default=None,
                ),
                "faceted_max_workspace_leakage": max(
                    (
                        row["faceted"]["metrics"]["workspace_leakage"]
                        for row in comparable
                        if row["faceted"]["metrics"]["workspace_leakage"] is not None
                    ),
                    default=None,
                ),
                "experiment_usable": bool(facet_comparable),
                "decision": (
                    "no_facet_cases_in_fixed_gold"
                    if not facet_comparable
                    else "keep_facets_disabled_until_recall_improves_without_regression"
                    if regressed or not improved
                    else "needs_human_review_before_any_production_change"
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
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()

    gold = GoldSet.model_validate_json(args.gold.read_text(encoding="utf-8-sig"))
    report = run_experiment(workspace_id=args.workspace_id, gold=gold, top_k=args.top_k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Report written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
