"""Audit and optionally repair historical paper chunk indexes.

The default mode is read-only. It checks the current Paper pointers, the
immutable parsed_text artifact, the canonical chunk_index JSONL, and the live
workspace-scoped Milvus chunk IDs. ``--repair`` only rebuilds papers that have
an active parsed_text artifact; it creates a new chunk_index artifact and
force-reindexes Milvus without rerunning knowledge extraction.

Run from backend/:

    python scripts/reconcile_historical_chunks.py
    python scripts/reconcile_historical_chunks.py --repair --output ..\\evaluation\\graphrag\\reports\\chunk_repair.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import ValidationError

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.models  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.domains.artifact.models import Artifact  # noqa: E402
from app.domains.artifact.service import ArtifactService  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.retrieval import milvus_client  # noqa: E402
from app.domains.retrieval.schemas import ChunkRecord  # noqa: E402


DATA_ISSUES = {
    "parsed_text_artifact_missing",
    "parsed_text_file_missing",
    "chunk_index_artifact_missing",
    "chunk_index_file_missing",
    "chunk_json_invalid",
    "chunk_schema_invalid",
    "chunk_count_mismatch",
    "chunk_scope_mismatch",
    "chunk_source_artifact_mismatch",
    "chunk_id_missing",
    "chunk_id_duplicate",
    "chunk_index_mismatch",
    "chunk_range_invalid",
    "chunk_text_mismatch",
    "empty_chunk_index",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", help="Only inspect one workspace.")
    parser.add_argument(
        "--paper-id",
        action="append",
        dest="paper_ids",
        help="Only inspect the specified paper; repeat for multiple papers.",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Repair detected data/Milvus inconsistencies after the read-only audit.",
    )
    parser.add_argument(
        "--skip-milvus",
        action="store_true",
        help="Skip live Milvus ID verification.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the JSON audit (and post-repair audit) to this path.",
    )
    return parser.parse_args()


def _append_issue(result: dict[str, Any], issue: str) -> None:
    if issue not in result["issues"]:
        result["issues"].append(issue)


def _active_artifact(
    db: Session,
    artifact_id: str | None,
    *,
    workspace_id: str,
    kind: str,
) -> Artifact | None:
    if not artifact_id:
        return None
    artifact = db.execute(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.workspace_id == workspace_id,
            Artifact.is_deleted.is_(False),
            Artifact.kind == kind,
        )
    ).scalar_one_or_none()
    if artifact is None:
        return None
    return artifact


def _read_artifact(
    artifact_service: ArtifactService,
    artifact: Artifact | None,
) -> str:
    if artifact is None:
        return ""
    path = artifact_service.resolve_abs_path(artifact)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _load_jsonl(
    artifact_service: ArtifactService,
    artifact: Artifact | None,
    result: dict[str, Any],
) -> list[dict[str, Any]]:
    if artifact is None:
        _append_issue(result, "chunk_index_artifact_missing")
        return []
    path = artifact_service.resolve_abs_path(artifact)
    if not path.is_file():
        _append_issue(result, "chunk_index_file_missing")
        return []

    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            _append_issue(result, "chunk_json_invalid")
            result["invalid_lines"].append(line_number)
            continue
        if not isinstance(raw, dict):
            _append_issue(result, "chunk_json_invalid")
            result["invalid_lines"].append(line_number)
            continue
        records.append(raw)
    return records


def audit_paper(
    db: Session,
    paper: Paper,
    *,
    check_milvus: bool,
) -> dict[str, Any]:
    artifact_service = ArtifactService(db)
    result: dict[str, Any] = {
        "paper_id": paper.id,
        "workspace_id": paper.workspace_id,
        "title": paper.title,
        "parse_status": paper.parse_status,
        "chunk_count_declared": paper.chunk_count,
        "issues": [],
        "invalid_lines": [],
        "chunk_count_observed": 0,
        "valid_chunk_count": 0,
        "milvus_check": "skipped",
        "milvus_existing_count": None,
        "repair_candidate": False,
    }

    if paper.parse_status != "parsed":
        result["status"] = "skipped_not_parsed"
        return result

    text_artifact = _active_artifact(
        db,
        paper.parsed_text_artifact_id,
        workspace_id=paper.workspace_id,
        kind="parsed_text",
    )
    parsed_text = _read_artifact(artifact_service, text_artifact)
    if text_artifact is None:
        _append_issue(result, "parsed_text_artifact_missing")
    elif not parsed_text:
        _append_issue(result, "parsed_text_file_missing")
    result["parsed_text_artifact_id"] = text_artifact.id if text_artifact else None

    chunk_artifact = _active_artifact(
        db,
        paper.chunk_index_artifact_id,
        workspace_id=paper.workspace_id,
        kind="chunk_index",
    )
    result["chunk_index_artifact_id"] = chunk_artifact.id if chunk_artifact else None
    raw_records = _load_jsonl(artifact_service, chunk_artifact, result)
    result["chunk_count_observed"] = len(raw_records)
    if not raw_records:
        _append_issue(result, "empty_chunk_index")
    if len(raw_records) != paper.chunk_count:
        _append_issue(result, "chunk_count_mismatch")

    parsed_records: list[ChunkRecord] = []
    seen_ids: set[str] = set()
    for raw in raw_records:
        try:
            chunk = ChunkRecord.model_validate(raw)
        except ValidationError:
            _append_issue(result, "chunk_schema_invalid")
            continue
        parsed_records.append(chunk)
        if not chunk.chunk_id:
            _append_issue(result, "chunk_id_missing")
        elif chunk.chunk_id in seen_ids:
            _append_issue(result, "chunk_id_duplicate")
        seen_ids.add(chunk.chunk_id)
        if chunk.workspace_id != paper.workspace_id or chunk.paper_id != paper.id:
            _append_issue(result, "chunk_scope_mismatch")
        if text_artifact and chunk.source_artifact_id != text_artifact.id:
            _append_issue(result, "chunk_source_artifact_mismatch")
        if chunk.chunk_index != len(parsed_records) - 1:
            _append_issue(result, "chunk_index_mismatch")
        if (
            not parsed_text
            or chunk.start_char < 0
            or chunk.end_char <= chunk.start_char
            or chunk.end_char > len(parsed_text)
        ):
            _append_issue(result, "chunk_range_invalid")
        elif chunk.text != parsed_text[chunk.start_char : chunk.end_char]:
            _append_issue(result, "chunk_text_mismatch")

    result["valid_chunk_count"] = len(parsed_records)
    expected_ids = {chunk.chunk_id for chunk in parsed_records if chunk.chunk_id}

    if check_milvus and not result["issues"]:
        try:
            existing_ids = milvus_client.get_existing_chunk_ids(
                paper.id,
                workspace_id=paper.workspace_id,
            )
            result["milvus_check"] = "passed" if existing_ids == expected_ids else "mismatch"
            result["milvus_existing_count"] = len(existing_ids)
            if existing_ids != expected_ids:
                _append_issue(result, "milvus_ids_mismatch")
            result["milvus_missing_count"] = len(expected_ids - existing_ids)
            result["milvus_stale_count"] = len(existing_ids - expected_ids)
        except Exception as exc:  # pragma: no cover - depends on live infrastructure
            result["milvus_check"] = "unavailable"
            result["milvus_error"] = type(exc).__name__

    result["repair_candidate"] = bool(
        result["issues"]
        and (
            any(issue in DATA_ISSUES for issue in result["issues"])
            or "milvus_ids_mismatch" in result["issues"]
        )
        and bool(parsed_text)
    )
    if result["issues"]:
        result["status"] = "needs_repair" if result["repair_candidate"] else "needs_manual_review"
    else:
        result["status"] = "ok"
    return result


def _select_papers(
    db: Session,
    *,
    workspace_id: str | None,
    paper_ids: list[str] | None,
) -> list[Paper]:
    statement = select(Paper).where(Paper.is_deleted.is_(False))
    if workspace_id:
        statement = statement.where(Paper.workspace_id == workspace_id)
    if paper_ids:
        statement = statement.where(Paper.id.in_(paper_ids))
    return list(db.execute(statement.order_by(Paper.created_at)).scalars())


def _audit_all(
    *,
    workspace_id: str | None,
    paper_ids: list[str] | None,
    check_milvus: bool,
) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        papers = _select_papers(
            db,
            workspace_id=workspace_id,
            paper_ids=paper_ids,
        )
        return [audit_paper(db, paper, check_milvus=check_milvus) for paper in papers]
    finally:
        db.close()


def _repair_one(audit: dict[str, Any], *, skip_milvus: bool) -> dict[str, Any]:
    paper_id = audit["paper_id"]
    repair: dict[str, Any] = {"paper_id": paper_id, "action": None, "status": "skipped"}
    if not audit.get("repair_candidate"):
        return repair
    try:
        if any(issue in DATA_ISSUES for issue in audit["issues"]):
            from scripts.rebuild_paper_chunks import rebuild_paper

            rebuild_paper(
                paper_id,
                skip_milvus=skip_milvus,
                from_current_parsed_text=True,
            )
            repair["action"] = "rebuild_chunk_artifact_and_reindex"
        elif "milvus_ids_mismatch" in audit["issues"] and not skip_milvus:
            db = SessionLocal()
            try:
                from app.domains.retrieval.service import index_paper_chunks

                result = index_paper_chunks(
                    audit["workspace_id"],
                    paper_id,
                    db=db,
                    force_reindex=True,
                )
                if result.error:
                    raise RuntimeError(result.error)
            finally:
                db.close()
            repair["action"] = "force_reindex_milvus"
        else:
            return repair
        repair["status"] = "succeeded"
    except Exception as exc:  # pragma: no cover - depends on live artifacts/services
        repair["status"] = "failed"
        repair["error"] = f"{type(exc).__name__}: {exc}"
    return repair


def _summary(audits: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "papers": len(audits),
        "ok": sum(item.get("status") == "ok" for item in audits),
        "skipped_not_parsed": sum(item.get("status") == "skipped_not_parsed" for item in audits),
        "needs_repair": sum(item.get("status") == "needs_repair" for item in audits),
        "needs_manual_review": sum(item.get("status") == "needs_manual_review" for item in audits),
        "milvus_unavailable": sum(item.get("milvus_check") == "unavailable" for item in audits),
    }


def main() -> int:
    args = parse_args()
    audits = _audit_all(
        workspace_id=args.workspace_id,
        paper_ids=args.paper_ids,
        check_milvus=not args.skip_milvus,
    )
    report: dict[str, Any] = {
        "mode": "repair" if args.repair else "audit",
        "workspace_id": args.workspace_id,
        "summary": _summary(audits),
        "papers": audits,
    }

    if args.repair:
        repair_results = [
            _repair_one(audit, skip_milvus=args.skip_milvus)
            for audit in audits
            if audit.get("repair_candidate")
        ]
        report["repairs"] = repair_results
        post_audits = _audit_all(
            workspace_id=args.workspace_id,
            paper_ids=args.paper_ids,
            check_milvus=not args.skip_milvus,
        )
        report["post_repair_summary"] = _summary(post_audits)
        report["post_repair_papers"] = post_audits

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = args.output if args.output.is_absolute() else args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Report written to {output}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    if args.repair:
        print(json.dumps(report["post_repair_summary"], ensure_ascii=False))

    failed_repairs = any(item["status"] == "failed" for item in report.get("repairs", []))
    unresolved = report.get("post_repair_summary", report["summary"])
    return 2 if failed_repairs or unresolved["needs_repair"] or unresolved["needs_manual_review"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
