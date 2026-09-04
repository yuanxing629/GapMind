"""验证真实论文的解析、抽取、索引和检索。

从 backend/ 目录运行：

    python scripts/validate_pipeline.py \
        --workspace-id <workspace-uuid> \
        --paper-id <paper-1> --paper-id <paper-2> --paper-id <paper-3> \
        --query "self-interpretable graph neural network"

脚本只执行结构检查。检索相关性和 LLM 判断仍需要人工复核打印出的段落。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.db.models  # noqa: E402,F401  (register all SQLAlchemy models)
from app.db.session import SessionLocal  # noqa: E402
from app.domains.artifact.models import Artifact  # noqa: E402
from app.domains.artifact.service import ArtifactService  # noqa: E402
from app.domains.knowledge.models import EvidenceSpan, KnowledgeItem  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.retrieval import milvus_client  # noqa: E402
from app.domains.task.models import Task  # noqa: E402

TASK_TYPES = ("parse_pdf", "extract_knowledge", "embed_chunks")


class ValidationReport:
    def __init__(self) -> None:
        self.checks = 0
        self.failures: list[str] = []

    def check(self, condition: bool, message: str) -> None:
        self.checks += 1
        marker = "PASS" if condition else "FAIL"
        print(f"[{marker}] {message}")
        if not condition:
            self.failures.append(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument(
        "--paper-id",
        action="append",
        required=True,
        dest="paper_ids",
        help="Repeat for every paper to validate.",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Optional semantic query. Repeat to test multiple queries.",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000/api/v1",
    )
    parser.add_argument(
        "--skip-retrieval",
        action="store_true",
        help="Only validate DB artifacts, chunks, evidence, and task results.",
    )
    return parser.parse_args()


def read_artifact_text(
    artifact_service: ArtifactService,
    artifact: Artifact | None,
) -> str:
    if artifact is None:
        return ""
    path = artifact_service.resolve_abs_path(artifact)
    return path.read_text(encoding="utf-8") if path.exists() else ""


def latest_task(
    tasks: list[Task],
    *,
    paper_id: str,
    task_type: str,
) -> Task | None:
    matches = [
        task
        for task in tasks
        if task.task_type == task_type
        and (task.payload or {}).get("paper_id") == paper_id
    ]
    return max(matches, key=lambda task: task.created_at) if matches else None


def validate_paper(
    *,
    report: ValidationReport,
    db: Any,
    artifact_service: ArtifactService,
    workspace_id: str,
    paper_id: str,
    tasks: list[Task],
) -> str | None:
    paper = db.get(Paper, paper_id)
    report.check(paper is not None, f"{paper_id}: Paper exists")
    if paper is None:
        return None

    print(f"\nPaper: {paper.title}\nID: {paper.id}")
    belongs_to_workspace = paper.workspace_id == workspace_id
    report.check(
        belongs_to_workspace,
        f"{paper_id}: belongs to requested workspace",
    )
    if not belongs_to_workspace:
        print(
            f"Skipping remaining checks: actual workspace is "
            f"{paper.workspace_id}"
        )
        return None
    report.check(
        paper.parse_status == "parsed",
        f"{paper_id}: parse_status is parsed",
    )
    report.check(
        paper.extract_status == "extracted",
        f"{paper_id}: extract_status is extracted",
    )
    report.check(paper.chunk_count > 0, f"{paper_id}: has chunks")

    for task_type in TASK_TYPES:
        task = latest_task(tasks, paper_id=paper_id, task_type=task_type)
        report.check(task is not None, f"{paper_id}: has {task_type} task")
        if task:
            report.check(
                task.status == "succeeded",
                f"{paper_id}: latest {task_type} task succeeded",
            )
            if task_type == "embed_chunks" and task.result:
                indexed = int(task.result.get("indexed_count", 0))
                skipped = int(task.result.get("skipped_count", 0))
                total = int(task.result.get("total_chunks", 0))
                if indexed + skipped != total or total != paper.chunk_count:
                    print(
                        f"[INFO] {paper_id}: latest embed task recorded "
                        f"{total} chunks, current Paper has "
                        f"{paper.chunk_count}; checking live Milvus state"
                    )

    text_artifact = db.get(Artifact, paper.parsed_text_artifact_id)
    parsed_text = read_artifact_text(artifact_service, text_artifact)
    report.check(bool(parsed_text), f"{paper_id}: parsed_text is readable")

    chunk_artifact = (
        db.get(Artifact, paper.chunk_index_artifact_id)
        if paper.chunk_index_artifact_id
        else None
    )
    chunk_jsonl = read_artifact_text(artifact_service, chunk_artifact)
    report.check(
        chunk_artifact is not None,
        f"{paper_id}: chunk_index Artifact exists",
    )
    report.check(bool(chunk_jsonl), f"{paper_id}: chunk_index Artifact is readable")
    chunks: list[dict[str, Any]] = []
    if chunk_jsonl:
        chunks = [
            json.loads(line)
            for line in chunk_jsonl.splitlines()
            if line.strip()
        ]
    report.check(
        len(chunks) == paper.chunk_count,
        f"{paper_id}: JSONL count equals paper.chunk_count",
    )
    invalid_chunks = [
        chunk.get("chunk_index")
        for chunk in chunks
        if chunk.get("text")
        != parsed_text[
            int(chunk.get("start_char", 0)) : int(chunk.get("end_char", 0))
        ]
    ]
    report.check(
        bool(chunks) and not invalid_chunks,
        f"{paper_id}: all chunk texts equal parsed_text slices"
        + (f" (invalid: {invalid_chunks[:10]})" if invalid_chunks else ""),
    )
    try:
        indexed_ids = milvus_client.get_existing_chunk_ids(
            paper_id,
            workspace_id=workspace_id,
        )
        current_chunk_ids = {
            str(chunk.get("chunk_id"))
            for chunk in chunks
            if chunk.get("chunk_id")
        }
        report.check(
            indexed_ids == current_chunk_ids,
            f"{paper_id}: live Milvus chunk IDs match current chunk_index Artifact",
        )
    except Exception as exc:
        report.check(False, f"{paper_id}: Milvus verification failed: {exc}")

    items = list(
        db.execute(
            select(KnowledgeItem).where(
                KnowledgeItem.paper_id == paper_id,
                KnowledgeItem.is_deleted.is_(False),
            )
        ).scalars()
    )
    report.check(bool(items), f"{paper_id}: has knowledge items")

    invalid_evidence: list[str] = []
    evidence_count = 0
    artifact_text_cache: dict[str, str] = {}
    for item in items:
        spans = list(
            db.execute(
                select(EvidenceSpan).where(
                    EvidenceSpan.knowledge_item_id == item.id
                )
            ).scalars()
        )
        for span in spans:
            evidence_count += 1
            if not span.artifact_id:
                invalid_evidence.append(span.id)
                continue
            if span.artifact_id not in artifact_text_cache:
                artifact_text_cache[span.artifact_id] = read_artifact_text(
                    artifact_service,
                    db.get(Artifact, span.artifact_id),
                )
            source = artifact_text_cache[span.artifact_id]
            exact = (
                span.start_char is not None
                and span.end_char is not None
                and span.text == source[span.start_char : span.end_char]
            )
            if not exact:
                invalid_evidence.append(span.id)

    report.check(evidence_count > 0, f"{paper_id}: has evidence spans")
    report.check(
        not invalid_evidence,
        f"{paper_id}: all evidence texts equal artifact slices"
        + (
            f" (invalid span IDs: {invalid_evidence[:10]})"
            if invalid_evidence
            else ""
        ),
    )

    claim = next(
        (
            item.content.get("statement")
            for item in items
            if item.type == "claim"
            and isinstance(item.content, dict)
            and isinstance(item.content.get("statement"), str)
        ),
        None,
    )
    return claim


def print_hits(label: str, response: dict[str, Any]) -> list[dict[str, Any]]:
    print(f"\n{label}: status={response.get('status')} total={response.get('total')}")
    items = response.get("items")
    if not isinstance(items, list):
        return []
    for index, item in enumerate(items[:5], 1):
        text = str(item.get("text", "")).replace("\n", " ")[:220]
        print(
            f"  {index}. paper={item.get('paper_id')} "
            f"stage={item.get('retrieval_stage')} "
            f"judgement={item.get('judgement')} "
            f"confidence={item.get('judgement_confidence')} "
            f"score={item.get('score')}\n"
            f"     {text}"
        )
    return items


def post_json(
    client: httpx.Client,
    url: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = client.post(url, json=payload)
    response.raise_for_status()
    return response.json()


def validate_retrieval(
    *,
    report: ValidationReport,
    base_url: str,
    workspace_id: str,
    paper_ids: list[str],
    workspace_paper_ids: set[str],
    queries: list[str],
    claim_text: str | None,
) -> None:
    with httpx.Client(timeout=180.0) as client:
        for query in queries:
            result = post_json(
                client,
                f"{base_url}/workspaces/{workspace_id}/retrieval/search",
                {"query": query, "top_k": 10, "use_reranker": True},
            )
            items = print_hits(f"Semantic query: {query}", result)
            report.check(
                result.get("status") in {"succeeded", "degraded"},
                f"semantic search succeeded for: {query}",
            )
            report.check(
                all(item.get("paper_id") in workspace_paper_ids for item in items),
                f"semantic search has no paper outside workspace: {query}",
            )

        for paper_id in paper_ids:
            result = post_json(
                client,
                f"{base_url}/workspaces/{workspace_id}/retrieval/similar-work",
                {"paper_id": paper_id, "top_k": 10, "use_reranker": True},
            )
            items = print_hits(f"Similar work for: {paper_id}", result)
            report.check(
                all(item.get("paper_id") != paper_id for item in items),
                f"{paper_id}: similar-work excludes source paper",
            )

        if claim_text:
            result = post_json(
                client,
                f"{base_url}/workspaces/{workspace_id}/retrieval/counter-evidence",
                {
                    "claim_text": claim_text,
                    "top_k": 10,
                    "use_reranker": True,
                    "use_judge": True,
                },
            )
            print(f"\nClaim: {claim_text}")
            items = print_hits("Counter evidence", result)
            report.check(
                result.get("status") in {"succeeded", "degraded"},
                "counter-evidence completed",
            )
            report.check(
                all(
                    item.get("paper_id") in workspace_paper_ids
                    for item in items
                ),
                "counter-evidence has no paper outside workspace",
            )


def main() -> int:
    args = parse_args()
    report = ValidationReport()
    db = SessionLocal()
    claims: list[str] = []
    workspace_paper_ids: set[str] = set()

    try:
        artifact_service = ArtifactService(db)
        workspace_paper_ids = set(
            db.execute(
                select(Paper.id).where(
                    Paper.workspace_id == args.workspace_id,
                    Paper.is_deleted.is_(False),
                )
            ).scalars()
        )
        tasks = list(
            db.execute(
                select(Task).where(
                    Task.workspace_id == args.workspace_id,
                    Task.is_deleted.is_(False),
                )
            ).scalars()
        )
        for paper_id in args.paper_ids:
            claim = validate_paper(
                report=report,
                db=db,
                artifact_service=artifact_service,
                workspace_id=args.workspace_id,
                paper_id=paper_id,
                tasks=tasks,
            )
            if claim:
                claims.append(claim)
    finally:
        db.close()

    if not args.skip_retrieval:
        validate_retrieval(
            report=report,
            base_url=args.base_url.rstrip("/"),
            workspace_id=args.workspace_id,
            paper_ids=args.paper_ids,
            workspace_paper_ids=workspace_paper_ids,
            queries=args.query,
            claim_text=claims[0] if claims else None,
        )

    print("\n" + "=" * 80)
    print(f"Checks: {report.checks}")
    print(f"Failures: {len(report.failures)}")
    for failure in report.failures:
        print(f"  - {failure}")
    print("\nRetrieval passages above still require human relevance review.")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
