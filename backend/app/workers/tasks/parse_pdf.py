"""parse_pdf Celery task.

Phase 2 core: takes a paper_id, reads its PDF artifact, parses it into
text + chunks, writes derived artifacts, and updates the paper row's parsing state.

State flow:
    Paper row:    not_applicable / pending -> parsing -> parsed / failed
    Task row:     queued -> running -> succeeded / failed

The task talks to the DB through a fresh SessionLocal (NOT the FastAPI
request session - Celery runs in a separate process).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.logging import configure_logging, get_logger
from app.db.models import *  # noqa: F401,F403  - registers all ORM models on Base.metadata
from app.db.session import SessionLocal
from app.domains.artifact.chunker import chunk_parsed_pdf
from app.domains.artifact.document_parser import parse_document
from app.domains.artifact.models import Artifact
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
        paper.parse_error = error_msg
        paper.quality_flags = ["no_pdf"]
        db.commit()
        return {"status": "failed", "error": error_msg}

    # Mark paper as parsing
    paper.parse_status = "parsing"
    paper.parse_error = None
    paper.quality_flags = []
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
            paper.parse_error = str(e)[:4000]
            paper.quality_flags = ["parse_failed"]
            db.commit()
            try:
                from app.domains.discover.service import resume_discover_runs_for_paper

                resume_discover_runs_for_paper(db, paper.id, paper.workspace_id)
            except Exception as notify_error:
                logger.warning("parse_pdf.discover_notify_failed", paper_id=paper_id, error=str(notify_error))
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
    parse_result = parse_document(pdf_bytes)
    parsed = parse_result.parsed
    if not parsed.full_text.strip():
        raise RuntimeError(
            f"PDF produced no text (page_count={parsed.page_count}, "
            f"warnings={parsed.warnings})"
        )
    task_service.update_progress(task_id, 0.4)

    # 3. Save parsed_text first. Chunk offsets and source_artifact_id are
    # defined against this immutable text artifact.
    parsed_text_artifact = artifact_service.save_upload(
        workspace_id=paper.workspace_id,
        paper_id=paper.id,
        filename=f"{paper.id}_parsed_text.txt",
        content=parsed.full_text.encode("utf-8"),
        mime_type="text/plain",
        kind="parsed_text",
    )

    # 4. Chunk the exact parsed_text artifact content.
    created_at = datetime.now(timezone.utc).isoformat()
    chunks = chunk_parsed_pdf(
        parsed,
        workspace_id=paper.workspace_id,
        paper_id=paper.id,
        created_at=created_at,
        source_artifact_id=parsed_text_artifact.id,
    )
    task_service.update_progress(task_id, 0.6)

    # 5. Save parsed_markdown for extraction and evidence anchoring.
    parsed_md = parsed.to_markdown()
    parsed_md_artifact = artifact_service.save_upload(
        workspace_id=paper.workspace_id,
        paper_id=paper.id,
        filename=f"{paper.id}_{paper.title[:30]}_parsed.md".replace(" ", "_"),
        content=parsed_md.encode("utf-8"),
        mime_type="text/markdown",
        kind="parsed_markdown",
    )
    task_service.update_progress(task_id, 0.75)

    # 6. Save chunk_index artifact (a .jsonl file with all chunks).
    chunks_jsonl = "\n".join(json.dumps(_chunk_to_dict(c)) for c in chunks)
    chunk_index_artifact = artifact_service.save_upload(
        workspace_id=paper.workspace_id,
        paper_id=paper.id,
        filename=f"{paper.id}_chunks.jsonl",
        content=chunks_jsonl.encode("utf-8"),
        mime_type="application/jsonl",
        kind="chunk_index",
    )

    image_artifacts = []
    for image in parse_result.images:
        image_filename = image.relative_path.rsplit("/", 1)[-1] or "image"
        image_artifacts.append(
            artifact_service.save_upload(
                workspace_id=paper.workspace_id,
                paper_id=paper.id,
                filename=image_filename,
                content=image.content,
                mime_type=image.mime_type,
                kind="paper_image",
            )
        )
    task_service.update_progress(task_id, 0.9)

    # 7. Update paper row with parsing state.
    paper = db.get(Paper, paper.id)  # refresh to avoid stale state
    paper.parse_status = "parsed"
    paper.parsed_at = datetime.now(timezone.utc)
    paper.page_count = parsed.page_count
    paper.parsed_text_chars = len(parsed.full_text)
    paper.quality_flags = list(parsed.warnings)
    if not parsed.sections:
        paper.quality_flags.append("no_section_headings_detected")
    if any(not page.strip() for page in parsed.full_text.split("\f")):
        paper.quality_flags.append("blank_page_detected")
    paper.parse_error = None
    paper.chunk_count = len(chunks)
    paper.parsed_text_artifact_id = parsed_text_artifact.id
    paper.chunk_index_artifact_id = chunk_index_artifact.id
    paper.parsed_markdown_artifact_id = parsed_md_artifact.id
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
            "parsed_md_artifact_id": parsed_md_artifact.id,
            "chunk_index_artifact_id": chunk_index_artifact.id,
            "page_count": parsed.page_count,
            "parsed_text_chars": len(parsed.full_text),
            "quality_flags": paper.quality_flags,
            "parser_provider": parse_result.provider,
            "parser_backend": parse_result.backend,
            "parser_version": parse_result.version,
            "image_count": len(image_artifacts),
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
            "parsed_text_chars": len(parsed.full_text),
            "quality_flags": paper.quality_flags,
            "sections_detected": len(parsed.sections),
            "parser_provider": parse_result.provider,
            "parser_backend": parse_result.backend,
            "parser_version": parse_result.version,
            "parsed_text_artifact_id": parsed_text_artifact.id,
            "chunk_index_artifact_id": chunk_index_artifact.id,
            "image_count": len(image_artifacts),
        },
    )

    logger.info(
        "parse_pdf.succeeded",
        paper_id=paper.id,
        task_id=task_id,
        chunk_count=len(chunks),
        page_count=parsed.page_count,
        parser_provider=parse_result.provider,
    )

    # Spawn knowledge extraction task (Phase 3). Best-effort: if the
    # extract_knowledge task dispatch fails, the paper is still parsed
    # and the user can trigger extraction manually later.
    try:
        from app.workers.tasks.extract_knowledge import spawn_extract_knowledge

        spawn_extract_knowledge(db, paper.id, paper.workspace_id)
    except Exception as e:
        logger.warning(
            "parse_pdf.spawn_extract_failed",
            paper_id=paper.id,
            error=str(e),
        )

    # Spawn Milvus embedding/indexing task (Step ④). Best-effort:
    # if dispatch fails, chunks JSONL is still on disk for manual retry.
    try:
        from app.workers.tasks.embed_chunks import spawn_embed_chunks

        spawn_embed_chunks(db, paper.id, paper.workspace_id)
    except Exception as e:
        logger.warning(
            "parse_pdf.spawn_embed_failed",
            paper_id=paper.id,
            error=str(e),
        )

    try:
        from app.domains.discover.service import resume_discover_runs_for_paper

        resume_discover_runs_for_paper(db, paper.id, paper.workspace_id)
    except Exception as e:
        logger.warning("parse_pdf.discover_notify_failed", paper_id=paper.id, error=str(e))

    return {
        "status": "succeeded",
        "paper_id": paper.id,
        "chunk_count": len(chunks),
        "parsed_text_artifact_id": parsed_text_artifact.id,
        "parsed_md_artifact_id": parsed_md_artifact.id,
        "chunk_index_artifact_id": chunk_index_artifact.id,
        "parser_provider": parse_result.provider,
        "parser_backend": parse_result.backend,
        "parser_version": parse_result.version,
        "image_count": len(image_artifacts),
    }


def _chunk_to_dict(c) -> dict:
    """Serialize a Chunk dataclass to a JSON-compatible dict (Contract #1)."""
    return {
        "schema_version": "1.0.0",
        "chunk_id": c.chunk_id,
        "workspace_id": c.workspace_id,
        "paper_id": c.paper_id,
        "source_artifact_id": c.source_artifact_id,
        "source_artifact_kind": "parsed_text",
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
