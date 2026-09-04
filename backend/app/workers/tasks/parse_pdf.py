"""parse_pdf Celery 任务。

Phase 2 核心任务：接收 paper_id，读取 PDF Artifact，将其解析为文本和分块，
写入派生 Artifact，并更新论文行的解析状态。

状态流转：
    Paper 行：    not_applicable / pending -> parsing -> parsed / failed
    Task 行：     queued -> running -> succeeded / failed

任务通过新的 SessionLocal 访问数据库（不是 FastAPI 请求会话，Celery 在独立进程中运行）。
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
    """解析任务所属论文附加的 PDF。

    参数：
        task_id：由 spawn 流程创建的 Task 行 ID。Task 的 payload 必须包含
            {"paper_id": "..."}。
    """
    configure_logging()
    db: Session = SessionLocal()
    try:
        return _run_parse_pdf(db, task_id)
    finally:
        db.close()


def _run_parse_pdf(db: Session, task_id: str) -> dict:
    task_service = TaskService(db)

# queued -> running（校验状态转换并写入时间线）
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
# 没有可解析内容，同时将论文标记为 not_applicable。
        paper.parse_status = "not_applicable"
        paper.parse_error = error_msg
        paper.quality_flags = ["no_pdf"]
        db.commit()
        return {"status": "failed", "error": error_msg}

# 将论文标记为 parsing
    paper.parse_status = "parsing"
    paper.parse_error = None
    paper.quality_flags = []
    db.commit()

    try:
        result = _do_parse(db, paper, task_id, task_service)
        return result
    except Exception as e:
        logger.error("parse_pdf.failed", paper_id=paper_id, task_id=task_id, error=str(e))
# 将论文标记为 failed
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
# 将任务转换为 failed
        try:
            task_service.transition(task_id, "failed", error=str(e), progress=1.0)
        except Exception:
            pass
        return {"status": "failed", "error": str(e)}


def _do_parse(
    db: Session, paper: Paper, task_id: str, task_service: TaskService
) -> dict:
    """实际解析工作。假设 paper.parse_status 已经是 'parsing'。"""
    artifact_service = ArtifactService(db)

# 1. 从主 artifact 读取 PDF 字节。
    pdf_artifact = db.get(Artifact, paper.primary_artifact_id)
    if pdf_artifact is None or pdf_artifact.is_deleted:
        raise RuntimeError(f"primary artifact not found: {paper.primary_artifact_id}")

    pdf_path = artifact_service.resolve_abs_path(pdf_artifact)
    if not pdf_path.exists():
        raise RuntimeError(f"PDF file missing on disk: {pdf_path}")

    pdf_bytes = pdf_path.read_bytes()
    task_service.update_progress(task_id, 0.2)

# 2. 将 PDF 解析为文本和章节。
    parse_result = parse_document(pdf_bytes)
    parsed = parse_result.parsed
    if not parsed.full_text.strip():
        raise RuntimeError(
            f"PDF produced no text (page_count={parsed.page_count}, "
            f"warnings={parsed.warnings})"
        )
    task_service.update_progress(task_id, 0.4)

# 3. 先保存 parsed_text。分块偏移和 source_artifact_id 都以这个不可变的文本
# artifact 为基准。
    parsed_text_artifact = artifact_service.save_upload(
        workspace_id=paper.workspace_id,
        paper_id=paper.id,
        filename=f"{paper.id}_parsed_text.txt",
        content=parsed.full_text.encode("utf-8"),
        mime_type="text/plain",
        kind="parsed_text",
    )

# 4. 对 parsed_text artifact 的精确内容分块。
    created_at = datetime.now(timezone.utc).isoformat()
    chunks = chunk_parsed_pdf(
        parsed,
        workspace_id=paper.workspace_id,
        paper_id=paper.id,
        created_at=created_at,
        source_artifact_id=parsed_text_artifact.id,
    )
    task_service.update_progress(task_id, 0.6)

# 5. 保存 parsed_markdown，供知识抽取和证据定位使用。
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

# 6. 保存 chunk_index artifact（包含全部分块的 .jsonl 文件）。
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

# 7. 更新论文记录的解析状态。
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

# 8. 将任务转换为 succeeded。
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

# 9. 写入时间线事件。
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

# 启动知识抽取任务（Phase 3）。采用尽力执行策略：即使 extract_knowledge
# 任务派发失败，论文仍已完成解析，用户之后可以手动触发抽取。
    try:
        from app.workers.tasks.extract_knowledge import spawn_extract_knowledge

        spawn_extract_knowledge(db, paper.id, paper.workspace_id)
    except Exception as e:
        logger.warning(
            "parse_pdf.spawn_extract_failed",
            paper_id=paper.id,
            error=str(e),
        )

# 启动 Milvus 向量化/索引任务（步骤 ④）。采用尽力执行策略：即使派发失败，
# 分块 JSONL 仍保留在磁盘上，之后可以手动重试。
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
    """将 Chunk dataclass 序列化为 JSON 兼容字典（契约 #1）。"""
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
    """创建 Task 行并派发 parse_pdf Celery 任务。

    从论文上传/附加 PDF 流程调用。返回 task_id。
    """
# 确保已导入 parse_pdf 模块，使任务注册到 celery_app（`imports` 配置只在
# worker 进程中触发，不会在调用 .delay() 的 FastAPI 进程中触发）。
    import app.workers.tasks.parse_pdf  # noqa: F401  (import side-effect)

    task_service = TaskService(db)
    task = task_service.create(
        TaskCreate(
            workspace_id=workspace_id,
            task_type="parse_pdf",
            payload={"paper_id": paper_id},
        )
    )

# 派发 Celery 任务。传入 task_id，使 worker 能在执行过程中更新 Task 记录状态。
    async_result = parse_pdf_task.delay(task.id)
# 保存 celery_task_id，便于之后关联任务（取消等操作）。
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
