"""持久化 Discover Agent run 的 Celery 入口。"""

from __future__ import annotations

from app.db.session import SessionLocal
from app.domains.discover.models import DiscoverRun
from app.domains.discover.service import DiscoverRunCancelled, DiscoverService
from app.domains.task.service import TaskService
from app.workers.celery_app import celery_app


@celery_app.task(
    bind=True,
    name="gapmind.run_discover",
    autoretry_for=(TimeoutError,),
    retry_backoff=True,
    max_retries=2,
)
def run_discover_task(self, run_id: str) -> dict:
    del self
    db = SessionLocal()
    try:
        return DiscoverService(db).execute_run(run_id)
    except DiscoverRunCancelled:
        db.rollback()
        run = db.get(DiscoverRun, run_id)
        if run is not None:
            run.status = "cancelled"
            run.stage = "cancelled"
            db.commit()
            if run.task_id:
                try:
                    TaskService(db).transition(run.task_id, "cancelled", progress=run.progress)
                except Exception:
                    pass
        return {"run_id": run_id, "status": "cancelled"}
    except Exception as exc:
        db.rollback()
# 当 worker 异常不属于已明确处理的降级流水线结果时，不要让持久化 run 停留在
# `running`。run 仍可通过现有面向用户的重试路径再次执行，同时继续向 Celery
# 重新抛出原始异常。
        run = db.get(DiscoverRun, run_id)
        if run is not None and run.status not in {"succeeded", "cancelled", "failed"}:
            try:
                DiscoverService(db)._fail_run(
                    run,
                    "discover_worker_failed",
                    str(exc)[:4000],
                )
            except Exception:
                db.rollback()
        raise
    finally:
        db.close()


def spawn_discover_task(run_id: str) -> str:
    """派发 run 并返回 Celery 任务 id。"""
    result = run_discover_task.delay(run_id)
    return str(result.id)
