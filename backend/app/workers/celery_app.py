"""Celery 应用实例。

Phase 0：仅包含 app 和用于 health check 的 ping task。Domain task（parse_pdf、embed_chunks、
extract_knowledge）将在 Phase 2-3 注册。
"""

from __future__ import annotations

import sys

from celery import Celery
from celery.signals import worker_ready

# 导入模型注册表，使所有 ORM 模型在任务运行前加载到 Base.metadata。
# 否则只导入 Task + Paper 而未导入 Workspace 的 worker 会在提交时因
# NoReferencedTableError 失败，因为 SQLAlchemy 无法在不完整的模型集合中排序 FK 依赖。
import app.db.models  # noqa: F401  (import side-effect: registers all models)
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

celery_app = Celery(
    "gapmind",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=60 * 30,  # 30 min hard limit per task
    task_soft_time_limit=60 * 25,  # 25 min soft limit
    worker_prefetch_multiplier=1,  # fair scheduling for long tasks
    task_acks_late=True,  # re-deliver on worker crash
    task_default_queue="gapmind",
    imports=[
        "app.workers.tasks.parse_pdf",
        "app.workers.tasks.extract_knowledge",
        "app.workers.tasks.embed_chunks",
        "app.workers.tasks.run_discover",
        "app.workers.tasks.run_agent",
        "app.workers.tasks.extract_gap_annotation",
    ],
)

# Windows：默认 prefork 池使用 billiard SemLock，需要 CreateGlobalSemaphore 权限；
# 该权限经常被 Windows 安全策略阻止，导致每个子进程都因 WinError 5 崩溃。
# Solo 池在主进程中运行全部任务，可以完全规避该问题。对于 I/O 密集型的
# LLM/embedding 工作，进入 Phase 2+ 并需要并发后，可改用 `--pool=gevent`
#（先 `pip install gevent`）。
if sys.platform == "win32":
    celery_app.conf.update(worker_pool="solo")


@worker_ready.connect
def on_worker_ready(**_: object) -> None:
    configure_logging()
    logger.info("celery.worker.ready", broker=settings.celery_broker_url)


@celery_app.task(name="gapmind.ping")
def ping() -> dict[str, str]:
    """健康检查任务，返回 pong。"""
    return {"status": "pong", "worker": "gapmind"}
