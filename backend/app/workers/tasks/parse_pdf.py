"""parse_pdf Celery task.

Phase 2 core: takes a paper_id, reads its PDF artifact, parses it into
text + chunks, writes derived artifacts, exports chunks JSONL (Contract #1),
and updates the paper row's parsing state.

State flow:
    Paper row:    not_applicable / pending -> parsing -> parsed / failed
    Task row:     queued -> running -> succeeded / failed

The task talks to the DB through a fresh SessionLocal (NOT the FastAPI
request session - Celery runs in a separate process).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.models import *  # noqa: F401,F403  - registers all ORM models on Base.metadata
from app.db.session import SessionLocal
from app.domains.artifact.chunker import chunk_parsed_pdf
from app.domains.artifact.models import Artifact
from app.domains.artifact.pdf_parser import parse_pdf
from app.domains.artifact.service import ArtifactService
from app.domains.paper.models import Paper
from app.domains.task.schemas import TaskCreate
from app.domains.task.service import TaskService
from app.domains.timeline.service import TimelineService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="gapmind.parse_pdf", bind=True)
def parse_pdf_task(self, task_id: str) -> dict:
    """Parse the PDF attached to the task's paper.

    Args:
        task_id: The Task row ID created by the spawn flow. The task's
            payload must contain {"paper_id": "..."}.
    """
    configure_logging()
    db: Session = SessionLocal()
    try:
        return _run_parse_pdf(db, task_id)
    finally:
        db.close()


def _run_parse_pdf(db: Session, task_id: str) -> dict:
    task_service = TaskService(db)

    # queued -> running (validates transition, writes timeline)
    try:
        task = task_service.transition(task_id, "running", progress=0.05)
    except Exception as e:
        logger.error("parse_pdf.transition_failed", task_id=task_id, error=str(e))
        raise

    paper_id = task.payload.get("paper_id")
    if not paper_id:
        error_msg = "task payload missing 'paper_id'"
        task_service.transition(task_id, "failed", error=error_msg, progress=1.0)
        return {"status": "failed", "error": error_msg}

    paper = db.get(Paper, paper_id)
    if paper is None or paper.is_deleted:
        error_msg = f"paper not found or deleted: {paper_id}"
        task_service.transition(task_id, "failed", error=error_msg, progress=1.0)
        return {"status": "failed", "error": error_msg}

    if not paper.primary_artifact_id:
        error_msg = "paper has no primary_artifact_id (no PDF to parse)"
        task_service.transition(task_id, "failed", error=error_msg, progress=1.0)
        # Also mark paper as not_applicable since there's nothing to parse.
        paper.parse_status = "not_applicable"
        db.commit()
        return {"status": "failed", "error": error_msg}

    # Mark paper as parsing
    paper.parse_status = "parsing"
    db.commit()

    try:
        result = _do_parse(db, paper, task_id, task_service)
        return result
    except Exception as e:
        logger.error("parse_pdf.failed", paper_id=paper_id, task_id=task_id, error=str(e))
        # Mark paper as failed
        paper = db.get(Paper, paper_id)
        if paper is not None:
            paper.parse_status = "failed"
            db.commit()
        # Transition task to failed
        try:
            task_service.transition(task_id, "failed", error=str(e), progress=1.0)
        except Exception:
            pass
        return {"status": "failed", "error": str(e)}


def _do_parse(
    db: Session, paper: Paper, task_id: str, task_service: TaskService
) -> dict:
    """The actual parsing work. Assumes paper.parse_status is already 'parsing'."""
    artifact_service = ArtifactService(db)

    # 1. Read the PDF bytes from the primary artifact.
    pdf_artifact = db.get(Artifact, paper.primary_artifact_id)
    if pdf_artifact is None or pdf_artifact.is_deleted:
        raise RuntimeError(f"primary artifact not found: {paper.primary_artifact_id}")

    pdf_path = artifact_service.resolve_abs_path(pdf_artifact)
    if not pdf_path.exists():
        raise RuntimeError(f"PDF file missing on disk: {pdf_path}")

    pdf_bytes = pdf_path.read_bytes()
    task_service.update_progress(task_id, 0.2)

    # 2. Parse PDF into text + sections.
    parsed = parse_pdf(pdf_bytes)
    if not parsed.full_text.strip():
        raise RuntimeError(
            f"PDF produced no text (page_count={parsed.page_count}, "
            f"warnings={parsed.warnings})"
        )
    task_service.update_progress(task_id, 0.4)

    # 3. Chunk the parsed text. We set artifact_id="" here temporarily;
    # it gets filled in after we create the chunk_index artifact below.
    created_at = datetime.now(timezone.utc).isoformat()
    chunks = chunk_parsed_pdf(
        parsed,
        workspace_id=paper.workspace_id,
        paper_id=paper.id,
        artifact_id="",
        created_at=created_at,
    )
    task_service.update_progress(task_id, 0.6)

    # 4. Save parsed_text artifact (a .txt file with the full cleaned text).
    parsed_text_artifact = artifact_service.save_upload(
        workspace_id=paper.workspace_id,
        filename=f"{paper.id}_parsed_text.txt",
        content=parsed.full_text.encode("utf-8"),
        mime_type="text/plain",
        kind="parsed_text",
    )
    task_service.update_progress(task_id, 0.75)

    # 5. Save chunk_index artifact (a .jsonl file with all chunks).
    chunks_jsonl = "\n".join(json.dumps(_chunk_to_dict(c)) for c in chunks)
    chunk_index_artifact = artifact_service.save_upload(
        workspace_id=paper.workspace_id,
        filename=f"{paper.id}_chunks.jsonl",
        content=chunks_jsonl.encode("utf-8"),
        mime_type="application/jsonl",
        kind="chunk_index",
    )
    # Fill in artifact_id on each chunk to point to the chunk_index artifact.
    for c in chunks:
        c.artifact_id = chunk_index_artifact.id

    task_service.update_progress(task_id, 0.9)

    # 6. Export chunks to Contract #1 path: data/chunks/{ws}/{paper}.jsonl
    _export_chunks_jsonl(paper.workspace_id, paper.id, chunks)

    # 7. Update paper row with parsing state.
    paper = db.get(Paper, paper.id)  # refresh to avoid stale state
    paper.parse_status = "parsed"
    paper.parsed_at = datetime.now(timezone.utc)
    paper.chunk_count = len(chunks)
    paper.parsed_text_artifact_id = parsed_text_artifact.id
    paper.chunk_index_artifact_id = chunk_index_artifact.id
    db.commit()
    db.refresh(paper)

    # 8. Transition task to succeeded.
    task_service.transition(
        task_id,
        "succeeded",
        progress=1.0,
        result={
            "chunk_count": len(chunks),
            "parsed_text_artifact_id": parsed_text_artifact.id,
            "chunk_index_artifact_id": chunk_index_artifact.id,
            "page_count": parsed.page_count,
        },
    )

    # 9. Timeline event.
    TimelineService(db).record(
        workspace_id=paper.workspace_id,
        event_type="paper.parsed",
        subject_type="paper",
        subject_id=paper.id,
        payload={
            "chunk_count": len(chunks),
            "page_count": parsed.page_count,
            "sections_detected": len(parsed.sections),
            "parsed_text_artifact_id": parsed_text_artifact.id,
            "chunk_index_artifact_id": chunk_index_artifact.id,
        },
    )

    logger.info(
        "parse_pdf.succeeded",
        paper_id=paper.id,
        task_id=task_id,
        chunk_count=len(chunks),
        page_count=parsed.page_count,
    )
    return {
        "status": "succeeded",
        "paper_id": paper.id,
        "chunk_count": len(chunks),
        "parsed_text_artifact_id": parsed_text_artifact.id,
        "chunk_index_artifact_id": chunk_index_artifact.id,
    }


def _chunk_to_dict(c) -> dict:
    """Serialize a Chunk dataclass to a JSON-compatible dict (Contract #1)."""
    return {
        "chunk_id": c.chunk_id,
        "workspace_id": c.workspace_id,
        "paper_id": c.paper_id,
        "artifact_id": c.artifact_id,
        "chunk_index": c.chunk_index,
        "section": c.section,
        "subsection": c.subsection,
        "text": c.text,
        "start_char": c.start_char,
        "end_char": c.end_char,
        "page_start": c.page_start,
        "page_end": c.page_end,
        "tokens_estimate": c.tokens_estimate,
        "chunk_version": c.chunk_version,
        "created_at": c.created_at,
    }


def _export_chunks_jsonl(workspace_id: str, paper_id: str, chunks: list) -> None:
    """Write chunks to data/chunks/{workspace_id}/{paper_id}.jsonl (Contract #1).

    This is the file队友 zwx (RAG) consumes to build the Milvus index.
    The path is relative to the backend/ directory (where the worker runs).
    """
    export_root = Path(settings.app_storage_dir).resolve().parent / "data" / "chunks"
    export_dir = export_root / workspace_id
    export_dir.mkdir(parents=True, exist_ok=True)
    export_path = export_dir / f"{paper_id}.jsonl"

    lines = [json.dumps(_chunk_to_dict(c)) for c in chunks]
    export_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(
        "chunks.exported",
        paper_id=paper_id,
        workspace_id=workspace_id,
        path=str(export_path),
        chunk_count=len(chunks),
    )


def spawn_parse_pdf_task(db: Session, paper_id: str, workspace_id: str) -> str:
    """Create a Task row and dispatch the parse_pdf Celery task.

    Called from the paper upload/attach-pdf flow. Returns the task_id.
    """
    # Ensure the parse_pdf module is imported so the task is registered on
    # celery_app (the `imports` config only triggers in worker process, not
    # in the FastAPI process that calls .delay()).
    import app.workers.tasks.parse_pdf  # noqa: F401  (import side-effect)

    task_service = TaskService(db)
    task = task_service.create(
        TaskCreate(
            workspace_id=workspace_id,
            task_type="parse_pdf",
            payload={"paper_id": paper_id},
        )
    )

    # Dispatch the Celery task. We pass the task_id so the worker can update
    # the Task row's state as it progresses.
    async_result = parse_pdf_task.delay(task.id)
    # Persist the celery_task_id so we can correlate later (cancel, etc.).
    task.celery_task_id = async_result.id
    db.commit()

    logger.info(
        "parse_pdf.spawned",
        paper_id=paper_id,
        workspace_id=workspace_id,
        task_id=task.id,
        celery_task_id=async_result.id,
    )
    return task.id
