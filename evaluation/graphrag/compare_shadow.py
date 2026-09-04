"""Compare dense-only and GraphRAG shadow observation exports.

The script is intentionally dependency-free and diagnostic-only. It validates
the persisted temporary path contract, then reports counts for later human
review; it never decides whether GraphRAG is better.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any


def _load(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        records = payload.get("records")
        if isinstance(records, list):
            return [item for item in records if isinstance(item, dict)]
        return [payload]
    raise ValueError(f"{path} must contain an object or list of objects")


def _audit(record: dict[str, Any]) -> dict[str, Any]:
    audit = record.get("retrieval_audit", record)
    if not isinstance(audit, dict):
        return {}
    if "workspace_id" not in audit and record.get("workspace_id"):
        return {**audit, "workspace_id": record["workspace_id"]}
    return audit


def _graph_integrity(audit: dict[str, Any]) -> dict[str, int]:
    graph = audit.get("graph") or {}
    workspace_id = audit.get("workspace_id")
    invalid_paths = 0
    workspace_violations = 0
    path_count = 0
    for path in graph.get("paths") or []:
        if not isinstance(path, dict):
            invalid_paths += 1
            continue
        path_count += 1
        path_workspace = path.get("workspace_id")
        if workspace_id and path_workspace != workspace_id:
            workspace_violations += 1
        node_ids = {
            node.get("id")
            for node in path.get("nodes") or []
            if isinstance(node, dict)
        }
        for edge in path.get("edges") or []:
            if (
                not isinstance(edge, dict)
                or edge.get("source") not in node_ids
                or edge.get("target") not in node_ids
                or (path_workspace and edge.get("workspace_id") != path_workspace)
            ):
                invalid_paths += 1
    return {
        "path_count": path_count,
        "invalid_path_edges": invalid_paths,
        "workspace_violations": workspace_violations,
    }


def summarize(records: list[dict[str, Any]], *, include_graph: bool) -> dict[str, Any]:
    audits = [_audit(record) for record in records]
    result: dict[str, Any] = {
        "request_count": len(audits),
        "mean_returned_chunk_count": round(
            mean(float(audit.get("returned_chunk_count") or 0) for audit in audits), 3
        ) if audits else 0.0,
    }
    if not include_graph:
        return result
    integrity = {key: 0 for key in ("path_count", "invalid_path_edges", "workspace_violations")}
    graph_audits = [audit.get("graph") or {} for audit in audits]
    for audit in audits:
        for key, value in _graph_integrity(audit).items():
            integrity[key] += value
    result.update(integrity)
    result["fallback_count"] = sum(bool(graph.get("fallback")) for graph in graph_audits)
    result["truncated_count"] = sum(bool(graph.get("truncated")) for graph in graph_audits)
    result["supporting_paper_count"] = sum(
        len(graph.get("supporting_paper_ids") or []) for graph in graph_audits
    )
    result["supporting_evidence_count"] = sum(
        len(graph.get("supporting_evidence_ids") or []) for graph in graph_audits
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dense", required=True, type=Path)
    parser.add_argument("--shadow", required=True, type=Path)
    args = parser.parse_args()
    dense = _load(args.dense)
    shadow = _load(args.shadow)
    print(json.dumps({
        "dense_only": summarize(dense, include_graph=False),
        "dense_plus_sql_graph_shadow": summarize(shadow, include_graph=True),
        "interpretation": "diagnostic_only_human_review_required",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
