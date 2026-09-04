"""持久化 workspace agent 的 Celery 入口。"""

from __future__ import annotations

from app.db.session import SessionLocal
from app.domains.agent.service import AgentService
from app.workers.celery_app import celery_app


@celery_app.task(name="gapmind.run_agent", bind=True)
def run_agent_task(self, run_id: str) -> dict:
    del self
    db = SessionLocal()
    try:
        return AgentService(db).execute(run_id)
    finally:
        db.close()


def spawn_agent_task(run_id: str) -> str:
    return str(run_agent_task.delay(run_id).id)
