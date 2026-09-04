"""embed_chunks Celery 任务（Phase 3，步骤 ④）。

读取论文的 chunk_index Artifact（Contract B），通过 BGE-M3 向量化并将向量写入 Milvus。
parse_pdf 成功后自动触发。

状态流转：
    Task 行：queued -> running -> succeeded / failed
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal
from app.domains.paper.models import Paper
from app.domains.retrieval.service import index_paper_chunks
from app.domains.task.models import Task
from app.domains.task.schemas import TaskCreate
from app.domains.task.service import TaskNotFoundError, TaskService
from app.domains.timeline.service import TimelineService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="gapmind.embed_chunks", bind=True)
def embed_chunks_task(self, task_id: str) -> dict:
    """将论文分块向量化并索引到 Milvus。

    参数：
        task_id：Task 行 ID。Payload 必须包含 {"paper_id": "..."}。
    """
    configure_logging()
    db: Session = SessionLocal()
    try:
        try:
            result = _run_embed(db, task_id)
        except TaskNotFoundError:
# 对应的数据库任务被移除或数据库重置后，broker 中可能仍有过期消息。
            logger.warning(
                "embed_chunks.orphaned_task",
                task_id=task_id,
            )
            return {
                "status": "discarded",
                "error": f"task not found: {task_id}",
            }

        if result.get("status") == "failed":
            raise RuntimeError(result.get("error") or "embed_chunks failed")
        return result
    finally:
        db.close()


def _run_embed(db: Session, task_id: str) -> dict:
    task_service = TaskService(db)

    try:
        task_service.transition(task_id, "running", progress=0.1)
    except Exception as e:
        logger.error("embed_chunks.transition_failed", task_id=task_id, error=str(e))
        raise

    task = db.get(Task, task_id)
    if task is None:
        return {"status": "failed", "error": f"task not found: {task_id}"}

    paper_id = (task.payload or {}).get("paper_id")
    if not paper_id:
        return _fail(task_service, task_id, "task payload missing 'paper_id'")

    paper = db.get(Paper, paper_id)
    if paper is None or paper.is_deleted:
        return _fail(task_service, task_id, f"paper not found: {paper_id}")

    workspace_id = paper.workspace_id
    task_service.update_progress(task_id, 0.2)

    try:
        result = index_paper_chunks(workspace_id, paper_id, db=db)
    except Exception as e:
        logger.error(
            "embed_chunks.index_failed",
            task_id=task_id,
            paper_id=paper_id,
            error=str(e),
        )
        result = _fail(task_service, task_id, str(e))
        _notify_discover(db, paper_id, workspace_id)
        return result

    if result.error:
        failure = _fail(task_service, task_id, result.error)
        _notify_discover(db, paper_id, workspace_id)
        return failure

    task_service.transition(
        task_id,
        "succeeded",
        progress=1.0,
        result={
            "indexed_count": result.indexed_count,
            "skipped_count": result.skipped_count,
            "total_chunks": result.total_chunks,
            "duration_ms": round(result.duration_ms, 1),
        },
    )

# 记录时间线事件
    TimelineService(db).record(
        workspace_id=workspace_id,
        event_type="paper.indexed",
        subject_type="paper",
        subject_id=paper_id,
        payload={
            "indexed_chunks": result.indexed_count,
            "skipped_chunks": result.skipped_count,
            "embedding_model": result.embedding_model,
        },
    )
    db.commit()
    _notify_discover(db, paper_id, workspace_id)

    logger.info(
        "embed_chunks.succeeded",
        paper_id=paper_id,
        task_id=task_id,
        indexed=result.indexed_count,
        skipped=result.skipped_count,
    )
    return {
        "status": "succeeded",
        "indexed_count": result.indexed_count,
        "skipped_count": result.skipped_count,
        "total_chunks": result.total_chunks,
    }


def _fail(task_service: TaskService, task_id: str, error: str) -> dict:
    task_service.transition(task_id, "failed", error=error, progress=1.0)
    return {"status": "failed", "error": error}


def _notify_discover(db: Session, paper_id: str, workspace_id: str) -> None:
    try:
        from app.domains.discover.service import resume_discover_runs_for_paper

        resume_discover_runs_for_paper(db, paper_id, workspace_id)
    except Exception as exc:
        logger.warning("embed_chunks.discover_notify_failed", paper_id=paper_id, error=str(exc))


def spawn_embed_chunks(db: Session, paper_id: str, workspace_id: str) -> str:
    """创建 Task 行并派发 embed_chunks。

    在 parse_pdf 成功后调用，返回 task_id。该操作幂等：如果该论文已有活跃的 embed_chunks
    task，则直接返回已有 task。
    """
    import app.workers.tasks.embed_chunks  # noqa: F401

# 检查已有的活动任务
    active_tasks = db.execute(
        select(Task).where(
            Task.workspace_id == workspace_id,
            Task.task_type == "embed_chunks",
            Task.status.in_(["queued", "running"]),
            Task.is_deleted.is_(False),
        )
    ).scalars()
    for active_task in active_tasks:
        if (active_task.payload or {}).get("paper_id") == paper_id:
            return active_task.id

    task_service = TaskService(db)
    task = task_service.create(
        TaskCreate(
            workspace_id=workspace_id,
            task_type="embed_chunks",
            payload={"paper_id": paper_id},
        )
    )
    async_result = embed_chunks_task.delay(task.id)
    task.celery_task_id = async_result.id
    db.commit()

    logger.info(
        "embed_chunks.spawned",
        paper_id=paper_id,
        task_id=task.id,
        celery_task_id=async_result.id,
    )
    return task.id
