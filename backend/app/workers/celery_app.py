"""Celery application instance.

Phase 0: just the app + a ping task for health checks. Domain tasks
(parse_pdf, embed_chunks, extract_knowledge) will be registered in Phase 2-3.
"""

from __future__ import annotations

import sys

from celery import Celery
from celery.signals import worker_ready

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

# Import the model registry so all ORM models are loaded on Base.metadata
# before any task runs. Without this, a worker that imports only Task + Paper
# (but not Workspace) will fail on commit with NoReferencedTableError because
# SQLAlchemy can't sort FK dependencies across the partial model set.
import app.db.models  # noqa: F401  (import side-effect: registers all models)

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
        # Phase 3+ tasks will be listed here:
        # "app.workers.tasks.embed_chunks",
        # "app.workers.tasks.extract_knowledge",
    ],
)

# Windows: the default prefork pool uses billiard SemLock which requires
# CreateGlobalSemaphore permission that's often blocked by Windows security
# policy, causing every child process to crash with WinError 5. Solo pool
# runs everything in the main process and avoids this entirely. For I/O-bound
# LLM/embedding work we can switch to `--pool=gevent` (after `pip install
# gevent`) once we need concurrency in Phase 2+.
if sys.platform == "win32":
    celery_app.conf.update(worker_pool="solo")


@worker_ready.connect
def on_worker_ready(**_: object) -> None:
    configure_logging()
    logger.info("celery.worker.ready", broker=settings.celery_broker_url)


@celery_app.task(name="gapmind.ping")
def ping() -> dict[str, str]:
    """Health check task - returns pong."""
    return {"status": "pong", "worker": "gapmind"}
