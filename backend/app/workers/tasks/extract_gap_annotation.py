"""微调 Schema 3.0 论文抽取的 Celery 任务。"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.domains.artifact.models import Artifact
from app.domains.artifact.service import ArtifactService
from app.domains.gap.context import (
    KNOWLEDGE_CONTEXT_MODE,
    GapContextIdentity,
    GapKnowledgeExtractionPendingError,
    build_gap_context,
    get_gap_context_identity,
)
from app.domains.gap.models import PaperGapAnnotation
from app.domains.gap.prompt import PROMPT_VERSION
from app.domains.gap.service import GapService
from app.domains.gap.validation import classify_failure_kind
from app.domains.paper.models import Paper
from app.domains.task.models import Task
from app.domains.task.schemas import TaskCreate
from app.domains.task.service import TaskService
from app.gateway.gap_extractor import (
    GapExtractor,
    GapExtractorUnavailableError,
    OllamaGapExtractor,
    RemoteGapExtractor,
)
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="gapmind.extract_gap_annotation", bind=True)
def extract_gap_annotation_task(self, task_id: str) -> dict:
    configure_logging()
    db: Session = SessionLocal()
    try:
        return _run_gap_extraction(db, task_id)
    except Exception as exc:
        db.rollback()
        try:
            task = db.get(Task, task_id)
            if task is not None and task.status == "running":
                TaskService(db).transition(
                    task_id, "failed", progress=1.0, error=str(exc)
                )
        except Exception:
            db.rollback()
        logger.exception("gap_extraction.failed", task_id=task_id, error=str(exc))
        raise
    finally:
        db.close()


def _run_gap_extraction(
    db: Session,
    task_id: str,
    *,
    extractor: GapExtractor | None = None,
) -> dict:
    tasks = TaskService(db)
    task = tasks.transition(task_id, "running", progress=0.05)
    paper_id = str((task.payload or {}).get("paper_id") or "")
    force = bool((task.payload or {}).get("force"))
    paper = db.get(Paper, paper_id)
    if paper is None or paper.is_deleted or paper.workspace_id != task.workspace_id:
        return _fail(tasks, task_id, f"paper not found in workspace: {paper_id}")
    if not paper.parsed_markdown_artifact_id:
        return _fail(tasks, task_id, "paper has no parsed_markdown_artifact")
    artifact = db.get(Artifact, paper.parsed_markdown_artifact_id)
    if artifact is None or artifact.is_deleted:
        return _fail(tasks, task_id, "parsed markdown artifact not found")
    path = ArtifactService(db).resolve_abs_path(artifact)
    if not path.exists():
        return _fail(tasks, task_id, f"parsed markdown file missing: {path}")
    context = build_gap_context(db, paper, path.read_text(encoding="utf-8"))
    logger.info(
        "gap_extraction.context_selected",
        task_id=task_id,
        paper_id=paper.id,
        input_mode=context.input_mode,
        knowledge_extraction_run_id=context.knowledge_extraction_run_id,
        knowledge_item_count=len(context.knowledge_item_ids),
        evidence_span_count=len(context.evidence_span_ids),
        context_char_count=context.context_char_count,
        fallback_reason=context.fallback_reason,
    )
    if settings.gap_extraction_require_knowledge and context.input_mode != KNOWLEDGE_CONTEXT_MODE:
        return _fail(
            tasks,
            task_id,
            "knowledge extraction is required before gap extraction",
            result={
                "dependency_status": "knowledge_extraction_required",
                "input_mode": context.input_mode,
                "knowledge_extraction_run_id": context.knowledge_extraction_run_id,
                "fallback_reason": context.fallback_reason,
            },
        )
    markdown = context.text
    if not markdown:
        return _fail(
            tasks,
            task_id,
            "knowledge context is unavailable and legacy Markdown fallback is disabled",
            result={
                "dependency_status": "knowledge_extraction_required",
                "input_mode": context.input_mode,
                "knowledge_extraction_run_id": context.knowledge_extraction_run_id,
                "fallback_reason": context.fallback_reason,
            },
        )
    input_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    context_identity = GapContextIdentity(
        input_mode=context.input_mode,
        knowledge_extraction_run_id=context.knowledge_extraction_run_id,
        knowledge_context_sha256=context.knowledge_context_sha256,
        fallback_reason=context.fallback_reason,
    )

    if not force:
        existing = _get_valid_annotation(db, paper_id, context_identity)
        if existing is not None:
            result = {
                "annotation_id": existing.id,
                "status": "valid",
                "provider": existing.model_provider,
                "idempotent": True,
            }
            tasks.transition(task_id, "succeeded", progress=1.0, result=result)
            return result

    row = db.execute(
        select(PaperGapAnnotation).where(
            PaperGapAnnotation.paper_id == paper.id,
            PaperGapAnnotation.input_sha256 == input_sha256,
            PaperGapAnnotation.model_name == settings.gap_extractor_model,
            PaperGapAnnotation.prompt_version == PROMPT_VERSION,
            PaperGapAnnotation.input_mode == context.input_mode,
            PaperGapAnnotation.knowledge_extraction_run_id
            == context.knowledge_extraction_run_id,
            PaperGapAnnotation.is_deleted.is_(False),
        )
    ).scalar_one_or_none()
    if row is not None and row.status == "valid" and not force:
        result = {"annotation_id": row.id, "status": "valid", "idempotent": True}
        tasks.transition(task_id, "succeeded", progress=1.0, result=result)
        return result
    if row is None:
        row = PaperGapAnnotation(
            id=str(uuid4()),
            workspace_id=paper.workspace_id,
            paper_id=paper.id,
            artifact_id=artifact.id,
            task_id=task_id,
            input_sha256=input_sha256,
            knowledge_extraction_run_id=context.knowledge_extraction_run_id,
            knowledge_context_sha256=context.knowledge_context_sha256,
            input_mode=context.input_mode,
            source_knowledge_item_ids=context.knowledge_item_ids,
            source_evidence_span_ids=context.evidence_span_ids,
            context_char_count=context.context_char_count,
            context_fallback_reason=context.fallback_reason,
            schema_version="3.0",
            prompt_version=PROMPT_VERSION,
            model_provider="ollama",
            model_name=settings.gap_extractor_model,
            model_digest=settings.gap_extractor_model_digest or None,
            model_parameters={},
            status="running",
            attempts=0,
            raw_responses=[],
            output=None,
            validation_errors=[],
            fallback_reason=None,
            is_deleted=False,
        )
        db.add(row)
    else:
        row.task_id = task_id
        row.knowledge_extraction_run_id = context.knowledge_extraction_run_id
        row.knowledge_context_sha256 = context.knowledge_context_sha256
        row.input_mode = context.input_mode
        row.source_knowledge_item_ids = context.knowledge_item_ids
        row.source_evidence_span_ids = context.evidence_span_ids
        row.context_char_count = context.context_char_count
        row.context_fallback_reason = context.fallback_reason
        row.status = "running"
        row.attempts = 0
        row.raw_responses = []
        row.output = None
        row.validation_errors = []
        row.fallback_reason = None
    db.commit()
    tasks.update_progress(task_id, 0.20)

    model = extractor or OllamaGapExtractor()
    row.model_parameters = model.model_parameters
    try:
        result = model.extract(markdown)
    except GapExtractorUnavailableError as exc:
        message = str(exc)
        _store_failed_annotation(
            row,
            provider="ollama",
            model=settings.gap_extractor_model,
            attempts=0,
            raw_responses=[],
            validation_errors=[message],
            fallback_reason="local_model_unavailable",
            failure_kind="model_unavailable",
        )
        db.commit()
        return _try_remote_fallback(
            db,
            tasks,
            task_id,
            row,
            markdown,
            local_error=message,
            local_status="unavailable",
            local_failure_kind="model_unavailable",
            force=force,
        )
    except RuntimeError:
        message = "本地研究空白模型返回空响应，请检查服务状态后重试。"
        _store_failed_annotation(
            row,
            provider="ollama",
            model=settings.gap_extractor_model,
            attempts=0,
            raw_responses=[],
            validation_errors=[message],
            fallback_reason="local_model_unavailable",
            failure_kind="model_unavailable",
        )
        db.commit()
        return _try_remote_fallback(
            db,
            tasks,
            task_id,
            row,
            markdown,
            local_error=message,
            local_status="unavailable",
            local_failure_kind="model_unavailable",
            force=force,
        )
    row.attempts = result.attempts
    row.raw_responses = result.raw_responses
    row.validation_errors = result.validation_errors
    row.output = result.output.model_dump(mode="json") if result.output else None
    row.model_provider = result.provider
    row.model_name = result.model or settings.gap_extractor_model
    row.model_parameters = {
        **model.model_parameters,
        "validation_error_categories": result.validation_error_categories,
    }
    row.status = "valid" if result.output else "invalid"
    db.commit()
    if result.output is None:
        failure_kind = classify_failure_kind(markdown, result.validation_errors)
        row.fallback_reason = (
            "local_validation_failed"
            if failure_kind == "invalid_output"
            else failure_kind
        )
        db.commit()
        return _try_remote_fallback(
            db,
            tasks,
            task_id,
            row,
            markdown,
            local_error=_failure_message(failure_kind),
            local_status="invalid",
            local_failure_kind=failure_kind,
            force=force,
        )

    GapService(db).assign_annotation(row)
    row.fallback_reason = None
    db.commit()
    succeeded = {
        "annotation_id": row.id,
        "status": "valid",
        "attempts": row.attempts,
        "provider": row.model_provider,
        "input_mode": row.input_mode,
        "knowledge_extraction_run_id": row.knowledge_extraction_run_id,
        "context_fallback_reason": row.context_fallback_reason,
    }
    tasks.transition(task_id, "succeeded", progress=1.0, result=succeeded)
    return succeeded


def _store_failed_annotation(
    row: PaperGapAnnotation,
    *,
    provider: str,
    model: str,
    attempts: int,
    raw_responses: list[str],
    validation_errors: list[str],
    fallback_reason: str,
    failure_kind: str,
) -> None:
    row.model_provider = provider
    row.model_name = model
    row.attempts = attempts
    row.raw_responses = raw_responses
    row.output = None
    row.validation_errors = validation_errors
    row.fallback_reason = fallback_reason
    row.status = "invalid"
    row.model_parameters = {
        **(row.model_parameters or {}),
        "failure_kind": failure_kind,
    }


def _failure_message(failure_kind: str) -> str:
    if failure_kind == "content_insufficient":
        return "论文 Markdown 内容不足，无法可靠生成研究空白标注；请补充解析内容后重试。"
    if failure_kind == "not_applicable":
        return "论文可能不适用于研究空白 Schema（例如综述或教程类），未生成空白标注。"
    return "gap annotation failed validation"


def _remote_is_configured() -> bool:
    return bool(
        settings.gap_extractor_remote_enabled
        and settings.gap_extractor_remote_base_url
        and settings.gap_extractor_remote_api_key
        and settings.gap_extractor_remote_model
    )


def _try_remote_fallback(
    db: Session,
    tasks: TaskService,
    task_id: str,
    local_row: PaperGapAnnotation,
    markdown: str,
    *,
    local_error: str,
    local_status: str,
    local_failure_kind: str,
    force: bool,
) -> dict:
    base_result = {
        "annotation_id": local_row.id,
        "status": local_status,
        "attempts": local_row.attempts,
        "validation_errors": local_row.validation_errors,
        "fallback_reason": local_row.fallback_reason,
        "provider": local_row.model_provider,
    }
    if local_status == "unavailable":
        base_result["retryable"] = True
    if local_failure_kind in {"content_insufficient", "not_applicable"}:
        return _fail(tasks, task_id, local_error, result=base_result)
    if not _remote_is_configured():
        logger.warning(
            "gap_extraction.remote_fallback_skipped",
            task_id=task_id,
            paper_id=local_row.paper_id,
            reason="remote_fallback_not_configured",
        )
        local_row.fallback_reason = "remote_fallback_not_configured"
        db.commit()
        base_result["fallback_reason"] = local_row.fallback_reason
        return _fail(tasks, task_id, local_error, result=base_result)

    remote = RemoteGapExtractor()
    logger.info(
        "gap_extraction.remote_fallback_started",
        task_id=task_id,
        paper_id=local_row.paper_id,
        model=remote.model,
        reason=(
            "local_model_unavailable"
            if local_status == "unavailable"
            else "local_validation_failed"
        ),
    )
    tasks.update_progress(task_id, 0.85)
    remote_row = _get_or_create_remote_row(
        db,
        local_row,
        model=remote.model,
        force=force,
    )
    if remote_row.status == "valid" and not force:
        GapService(db).assign_annotation(remote_row)
        succeeded = {
            "annotation_id": remote_row.id,
            "status": "valid",
            "attempts": remote_row.attempts,
            "provider": remote_row.model_provider,
            "fallback_reason": remote_row.fallback_reason,
            "remote_fallback": True,
        }
        tasks.transition(task_id, "succeeded", progress=1.0, result=succeeded)
        return succeeded

    remote_row.model_parameters = remote.model_parameters
    remote_row.fallback_reason = "local_model_unavailable" if local_status == "unavailable" else "local_validation_failed"
    try:
# JSON Output 只能保证 JSON 语法有效。adapter 会重新运行同一个语义校验器，
# 并在结果成为棋盘标注前将错误反馈给模型。
        remote_result = remote.extract(markdown)
    except GapExtractorUnavailableError as exc:
        message = str(exc)
        _store_failed_annotation(
            remote_row,
            provider=remote.provider,
            model=remote.model,
            attempts=0,
            raw_responses=[],
            validation_errors=[message],
            fallback_reason=remote_row.fallback_reason,
            failure_kind="remote_model_unavailable",
        )
        db.commit()
        return _fail(
            tasks,
            task_id,
            message,
            result={
                "annotation_id": remote_row.id,
                "status": "unavailable",
                "retryable": True,
                "provider": remote_row.model_provider,
                "fallback_reason": remote_row.fallback_reason,
                "local_error": local_error,
                "remote_fallback": True,
            },
        )

    remote_row.model_provider = remote_result.provider
    remote_row.model_name = remote_result.model or remote.model
    remote_row.model_parameters = {
        **remote.model_parameters,
        "validation_error_categories": remote_result.validation_error_categories,
    }
    remote_row.attempts = remote_result.attempts
    remote_row.raw_responses = remote_result.raw_responses
    remote_row.validation_errors = remote_result.validation_errors
    remote_row.output = remote_result.output.model_dump(mode="json") if remote_result.output else None
    remote_row.status = "valid" if remote_result.output else "invalid"
    db.commit()
    if remote_result.output is None:
        failure_kind = classify_failure_kind(markdown, remote_result.validation_errors)
        if failure_kind in {"content_insufficient", "not_applicable"}:
            remote_row.fallback_reason = failure_kind
        db.commit()
        return _fail(
            tasks,
            task_id,
            _failure_message(failure_kind),
            result={
                "annotation_id": remote_row.id,
                "status": "invalid",
                "attempts": remote_row.attempts,
                "validation_errors": remote_row.validation_errors,
                "provider": remote_row.model_provider,
                "fallback_reason": remote_row.fallback_reason,
                "local_error": local_error,
                "remote_fallback": True,
            },
        )

    GapService(db).assign_annotation(remote_row)
    succeeded = {
        "annotation_id": remote_row.id,
        "status": "valid",
        "attempts": remote_row.attempts,
        "provider": remote_row.model_provider,
        "fallback_reason": remote_row.fallback_reason,
        "remote_fallback": True,
    }
    tasks.transition(task_id, "succeeded", progress=1.0, result=succeeded)
    return succeeded


def _get_or_create_remote_row(
    db: Session,
    local_row: PaperGapAnnotation,
    *,
    model: str,
    force: bool,
) -> PaperGapAnnotation:
    row = db.execute(
        select(PaperGapAnnotation).where(
            PaperGapAnnotation.paper_id == local_row.paper_id,
            PaperGapAnnotation.input_sha256 == local_row.input_sha256,
            PaperGapAnnotation.model_name == model,
            PaperGapAnnotation.prompt_version == local_row.prompt_version,
            PaperGapAnnotation.input_mode == local_row.input_mode,
            PaperGapAnnotation.is_deleted.is_(False),
        )
    ).scalar_one_or_none()
    if row is None:
        row = PaperGapAnnotation(
            id=str(uuid4()),
            workspace_id=local_row.workspace_id,
            paper_id=local_row.paper_id,
            artifact_id=local_row.artifact_id,
            task_id=local_row.task_id,
            input_sha256=local_row.input_sha256,
            knowledge_extraction_run_id=local_row.knowledge_extraction_run_id,
            knowledge_context_sha256=local_row.knowledge_context_sha256,
            input_mode=local_row.input_mode,
            source_knowledge_item_ids=local_row.source_knowledge_item_ids,
            source_evidence_span_ids=local_row.source_evidence_span_ids,
            context_char_count=local_row.context_char_count,
            context_fallback_reason=local_row.context_fallback_reason,
            schema_version=local_row.schema_version,
            prompt_version=local_row.prompt_version,
            model_provider="remote",
            model_name=model,
            model_digest=None,
            model_parameters={},
            status="running",
            attempts=0,
            raw_responses=[],
            output=None,
            validation_errors=[],
            fallback_reason=None,
            is_deleted=False,
        )
        db.add(row)
    elif force or row.status != "valid":
        row.task_id = local_row.task_id
        row.status = "running"
        row.attempts = 0
        row.raw_responses = []
        row.output = None
        row.validation_errors = []
    row.knowledge_extraction_run_id = local_row.knowledge_extraction_run_id
    row.knowledge_context_sha256 = local_row.knowledge_context_sha256
    row.input_mode = local_row.input_mode
    row.source_knowledge_item_ids = local_row.source_knowledge_item_ids
    row.source_evidence_span_ids = local_row.source_evidence_span_ids
    row.context_char_count = local_row.context_char_count
    row.context_fallback_reason = local_row.context_fallback_reason
    db.flush()
    return row


def _fail(
    service: TaskService, task_id: str, error: str, *, result: dict | None = None
) -> dict:
    service.transition(task_id, "failed", progress=1.0, error=error, result=result)
    return {"status": "failed", "error": error, **(result or {})}


def _has_valid_annotation(
    db: Session, paper_id: str, context: GapContextIdentity | None = None
) -> bool:
    """判断论文是否已有有效标注。

    本地模型或配置的远程 fallback 产生的有效结果已经可以供棋盘使用。Prompt/model 版本
    用于记录重新抽取和审计所需的 provenance，不是让增量“抽取已解析论文”操作重新运行
    整个语料库的理由。显式 ``force=True`` 仍是重新抽取的选择路径。
    """
    return _get_valid_annotation(db, paper_id, context) is not None


def _get_valid_annotation(
    db: Session,
    paper_id: str,
    context: GapContextIdentity | None = None,
) -> PaperGapAnnotation | None:
    query = select(PaperGapAnnotation).where(
        PaperGapAnnotation.paper_id == paper_id,
        PaperGapAnnotation.status == "valid",
        PaperGapAnnotation.is_deleted.is_(False),
    )
    if context is not None:
        query = query.where(PaperGapAnnotation.input_mode == context.input_mode)
        if context.input_mode == KNOWLEDGE_CONTEXT_MODE:
            query = query.where(
                PaperGapAnnotation.knowledge_extraction_run_id
                == context.knowledge_extraction_run_id
            )
        elif context.knowledge_extraction_run_id is not None:
            query = query.where(
                PaperGapAnnotation.knowledge_extraction_run_id
                == context.knowledge_extraction_run_id
            )
        else:
            query = query.where(PaperGapAnnotation.knowledge_extraction_run_id.is_(None))
    return db.execute(query.order_by(PaperGapAnnotation.updated_at.desc()).limit(1)).scalars().first()


def spawn_gap_extraction(
    db: Session,
    paper_id: str,
    workspace_id: str,
    *,
    force: bool = False,
) -> tuple[str | None, bool]:
    """为论文创建或复用 gap 抽取任务。

    返回 ``(task_id, skipped)``。``skipped=True`` 表示论文已经有来自任意 provider/version
    的有效标注，且没有创建 task（因此大型语料库执行“抽取已解析论文”时只会为新论文入队）。
    当 prompt/model 迁移确实需要重新抽取时，使用 ``force=True``。
    """
    paper = db.get(Paper, paper_id)
    if paper is None or paper.is_deleted or paper.workspace_id != workspace_id:
        raise ValueError(f"paper not found in workspace: {paper_id}")
    if not paper.parsed_markdown_artifact_id:
        raise ValueError(f"paper has no parsed markdown: {paper_id}")
    if paper.extract_status in {"pending", "extracting"}:
        raise GapKnowledgeExtractionPendingError(
            "knowledge extraction is still running; gap extraction must wait"
        )

    context_identity = get_gap_context_identity(db, paper)
    if not force and _has_valid_annotation(db, paper_id, context_identity):
        return None, True

    active = db.execute(
        select(Task).where(
            Task.workspace_id == workspace_id,
            Task.task_type == "extract_gap_annotation",
            Task.status.in_(["queued", "running"]),
            Task.is_deleted.is_(False),
        )
    ).scalars()
    for item in active:
        if (item.payload or {}).get("paper_id") == paper_id:
            return item.id, False

    task = TaskService(db).create(
        TaskCreate(
            workspace_id=workspace_id,
            task_type="extract_gap_annotation",
            payload={
                "paper_id": paper_id,
                "force": force,
            },
        )
    )
    async_result = extract_gap_annotation_task.delay(task.id)
    task.celery_task_id = async_result.id
    db.commit()
    return task.id, False

