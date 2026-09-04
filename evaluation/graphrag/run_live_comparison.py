"""Run a read-only dense vs SQL GraphRAG-shadow retrieval comparison.

This runner uses the query text from an existing retrieval Gold Set but does
not modify the Gold Set, call an LLM, write Chat messages, or change database
state. Dense retrieval remains the only answer context. The GraphRAG side
only measures the bounded PostgreSQL projection and its EvidenceSpan
re-retrieval.

Run from the repository root:

    backend\\.venv\\Scripts\\python.exe evaluation\\graphrag\\run_live_comparison.py \
        --workspace-id <workspace-uuid> \
        --gold evaluation\\retrieval\\gold\\demo_sig_ood_v1.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
# The application is normally launched from backend/, where the default
# ``./storage`` resolves correctly. This evaluator is documented from the
# repository root, so pin the relative default to the same canonical store
# without overriding an explicit deployment setting.
os.environ.setdefault("APP_STORAGE_DIR", str(BACKEND_ROOT / "storage"))
for import_root in (str(REPO_ROOT), str(BACKEND_ROOT)):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

from sqlalchemy import select  # noqa: E402

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.knowledge.graphrag import build_bounded_projection  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.retrieval.service import (  # noqa: E402
    find_chunk_record,
    semantic_search,
)
from evaluation.graphrag.compare_shadow import _graph_integrity  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _load_gold_queries(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    queries: list[dict[str, str]] = []
    for item in payload.get("semantic_search", []):
        queries.append({
            "query_id": item["query_id"],
            "category": "semantic_search",
            "query": item["query"],
        })
    for item in payload.get("counter_evidence", []):
        queries.append({
            "query_id": item["query_id"],
            "category": "counter_evidence",
            "query": item["claim_text"],
        })
    if not queries:
        raise ValueError(f"No semantic_search or counter_evidence queries in {path}")
    return queries


def _paper_workspace_map(
    db,
    workspace_id: str,
    paper_ids: set[str],
) -> dict[str, str]:
    if not paper_ids:
        return {}
    return {
        paper.id: paper.workspace_id
        for paper in db.scalars(
            select(Paper).where(
                Paper.workspace_id == workspace_id,
                Paper.id.in_(paper_ids),
                Paper.is_deleted.is_(False),
            )
        )
    }


def _dense_evidence_stats(db, workspace_id: str, items: list[Any]) -> dict[str, Any]:
    returned = len(items)
    traceable = 0
    failure_reasons: dict[str, int] = {}

    def failed(reason: str) -> None:
        failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    def stored_text_matches(item_text: str, canonical_text: str) -> bool:
        # Milvus stores the first 8,000 characters to stay below its VARCHAR
        # limit. Identity is still established by chunk_id and artifact_id;
        # this check catches stale vectors without rejecting valid truncation.
        return item_text == canonical_text or (
            len(item_text) == 8000 and canonical_text.startswith(item_text)
        )

    paper_ids = {item.paper_id for item in items if item.paper_id}
    workspace_map = _paper_workspace_map(db, workspace_id, paper_ids)
    for item in items:
        if (
            not item.paper_id
            or not item.chunk_id
            or workspace_map.get(item.paper_id) != workspace_id
        ):
            failed("missing_identity_or_workspace")
            continue
        chunk = find_chunk_record(
            workspace_id,
            item.paper_id,
            item.chunk_id,
            db=db,
        )
        if (
            chunk is not None
            and chunk.workspace_id == workspace_id
            and chunk.paper_id == item.paper_id
            and chunk.source_artifact_id == item.artifact_id
            and stored_text_matches(item.text, chunk.text)
        ):
            traceable += 1
        elif chunk is None:
            failed("chunk_not_found")
        elif chunk.source_artifact_id != item.artifact_id:
            failed("source_artifact_mismatch")
        elif not stored_text_matches(item.text, chunk.text):
            failed("text_mismatch")
        else:
            failed("chunk_scope_mismatch")
    return {
        "returned_chunk_count": returned,
        "traceable_chunk_count": traceable,
        "evidence_hit_rate": traceable / returned if returned else None,
        "query_hit": returned > 0,
        "traceability_failure_reasons": failure_reasons,
    }


def _run_query(db, workspace_id: str, query: dict[str, str], top_k: int) -> dict[str, Any]:
    dense_started = time.perf_counter()
    dense = semantic_search(
        workspace_id,
        query["query"],
        top_k=top_k,
        use_reranker=True,
        diversify_by_paper=True,
    )
    dense_wall_ms = (time.perf_counter() - dense_started) * 1000
    dense_stats = _dense_evidence_stats(db, workspace_id, dense.items)
    entry: dict[str, Any] = {
        **query,
        "dense": {
            "status": dense.status,
            "diagnostic_code": dense.diagnostic_code,
            "latency_ms": round(dense_wall_ms, 2),
            "reported_retrieval_latency_ms": round(dense.latency_ms, 2),
            **dense_stats,
        },
        "graph_shadow": {
            "status": "not_run",
            "latency_ms": 0.0,
            "path_count": 0,
            "candidate_path_count": 0,
            "emitted_path_count": 0,
            "dropped_path_count": 0,
            "dropped_path_reasons": {},
            "paths_with_evidence": 0,
            "supporting_evidence_count": 0,
            "evidence_hit_rate": None,
            "query_evidence_hit": False,
            "fallback": True,
            "fallback_reason": "dense_retrieval_failed" if dense.status == "failed" else None,
        },
    }
    if dense.status == "failed":
        return entry

    graph_started = time.perf_counter()
    try:
        projection = build_bounded_projection(
            db,
            workspace_id=workspace_id,
            dense_items=dense.items,
            request_id=dense.request_id,
            query_text=query["query"],
            max_hops=2,
            node_limit=32,
            edge_limit=64,
        )
        graph_wall_ms = (time.perf_counter() - graph_started) * 1000
        path_count = len(projection.paths)
        paths_with_evidence = sum(bool(path.evidence) for path in projection.paths)
        evidence_count = len(projection.evidence)
        graph_stats = {
            "status": "succeeded",
            "latency_ms": round(graph_wall_ms, 2),
            "path_count": path_count,
            "candidate_path_count": projection.candidate_path_count,
            "emitted_path_count": projection.emitted_path_count,
            "dropped_path_count": projection.dropped_path_count,
            "dropped_path_reasons": projection.dropped_path_reasons,
            "paths_with_evidence": paths_with_evidence,
            "supporting_evidence_count": len(projection.supporting_evidence_ids),
            "evidence_hit_rate": (
                paths_with_evidence / path_count if path_count else 0.0
            ),
            "query_evidence_hit": evidence_count > 0,
            "fallback": evidence_count == 0,
            "fallback_reason": "insufficient_evidence" if evidence_count == 0 else None,
            "truncated": projection.truncated,
            "truncation_reason": projection.truncation_reason,
            **_graph_integrity({
                "workspace_id": workspace_id,
                "graph": {"paths": [path.model_dump(mode="json") for path in projection.paths]},
            }),
        }
        entry["graph_shadow"] = graph_stats
        entry["dense_plus_shadow_latency_ms"] = round(dense_wall_ms + graph_wall_ms, 2)
    except Exception as exc:  # pragma: no cover - live DB dependent
        graph_wall_ms = (time.perf_counter() - graph_started) * 1000
        entry["graph_shadow"] = {
            "status": "failed",
            "latency_ms": round(graph_wall_ms, 2),
            "path_count": 0,
            "candidate_path_count": 0,
            "emitted_path_count": 0,
            "dropped_path_count": 0,
            "dropped_path_reasons": {},
            "paths_with_evidence": 0,
            "supporting_evidence_count": 0,
            "evidence_hit_rate": None,
            "query_evidence_hit": False,
            "fallback": True,
            "fallback_reason": type(exc).__name__,
        }
        entry["dense_plus_shadow_latency_ms"] = round(dense_wall_ms + graph_wall_ms, 2)
    return entry


def _aggregate(entries: list[dict[str, Any]]) -> dict[str, Any]:
    dense = [entry["dense"] for entry in entries]
    graph = [entry["graph_shadow"] for entry in entries]

    def values(rows: list[dict[str, Any]], key: str) -> list[float]:
        return [float(row[key]) for row in rows if isinstance(row.get(key), (int, float))]

    dense_latency = values(dense, "latency_ms")
    graph_latency = values(graph, "latency_ms")
    dense_hit_rates = values(dense, "evidence_hit_rate")
    graph_hit_rates = values(graph, "evidence_hit_rate")
    graph_path_count = sum(int(row.get("path_count", 0)) for row in graph)
    graph_candidate_path_count = sum(
        int(row.get("candidate_path_count", 0)) for row in graph
    )
    graph_emitted_path_count = sum(
        int(row.get("emitted_path_count", row.get("path_count", 0))) for row in graph
    )
    graph_dropped_path_count = sum(
        int(row.get("dropped_path_count", 0)) for row in graph
    )
    graph_dropped_path_reasons: dict[str, int] = {}
    for row in graph:
        for reason, count in (row.get("dropped_path_reasons") or {}).items():
            graph_dropped_path_reasons[reason] = (
                graph_dropped_path_reasons.get(reason, 0) + int(count)
            )
    graph_paths_with_evidence = sum(
        int(row.get("paths_with_evidence", 0)) for row in graph
    )
    return {
        "query_count": len(entries),
        "dense_only": {
            "query_hit_rate": sum(row["query_hit"] for row in dense) / len(dense) if dense else 0.0,
            "evidence_hit_rate": mean(dense_hit_rates) if dense_hit_rates else None,
            "latency_ms": {
                "mean": mean(dense_latency) if dense_latency else None,
                "max": max(dense_latency) if dense_latency else None,
            },
        },
        "graph_shadow": {
            "query_evidence_hit_rate": sum(row["query_evidence_hit"] for row in graph) / len(graph) if graph else 0.0,
            "path_count": graph_path_count,
            "candidate_path_count": graph_candidate_path_count,
            "emitted_path_count": graph_emitted_path_count,
            "dropped_path_count": graph_dropped_path_count,
            "dropped_path_reasons": graph_dropped_path_reasons,
            "paths_with_evidence": graph_paths_with_evidence,
            # Keep the macro average for query-level comparison and expose
            # the micro rate so the denominator is unambiguous.
            "path_evidence_hit_rate": mean(graph_hit_rates) if graph_hit_rates else None,
            "path_evidence_hit_rate_micro": (
                graph_paths_with_evidence / graph_path_count
                if graph_path_count
                else None
            ),
            "fallback_count": sum(bool(row["fallback"]) for row in graph),
            "truncated_count": sum(bool(row.get("truncated")) for row in graph),
            "latency_ms": {
                "mean": mean(graph_latency) if graph_latency else None,
                "max": max(graph_latency) if graph_latency else None,
            },
        },
        "dense_plus_shadow": {
            "estimated_mean_latency_ms": mean(
                values(entries, "dense_plus_shadow_latency_ms")
            ) if values(entries, "dense_plus_shadow_latency_ms") else None,
        },
    }


def main() -> int:
    args = parse_args()
    queries = _load_gold_queries(args.gold)
    db = SessionLocal()
    try:
        entries = [_run_query(db, args.workspace_id, query, args.top_k) for query in queries]
    finally:
        db.close()

    report = {
        "schema_version": "1.1.0",
        "evaluation": "dense_only_vs_sql_graph_shadow",
        "workspace_id": args.workspace_id,
        "gold_source": str(args.gold),
        "top_k": args.top_k,
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scope": {
            "answer_context": "dense_only",
            "graph_database": "none",
            "graph_projection": "postgresql_bounded_sql",
            "llm_called": False,
            "database_mutated": False,
            "evidence_hit_rate_definition": (
                "dense: traceable returned chunks / returned chunks, with exact "
                "text or the canonical prefix stored under Milvus's "
                "8,000-character limit; "
                "graph: paths with re-retrieved EvidenceSpan / paths"
            ),
        },
        "summary": _aggregate(entries),
        "queries": entries,
    }
    output = args.output if args.output.is_absolute() else args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"Report written to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
