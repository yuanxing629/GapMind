"""API v1 路由聚合器。

领域路由会在实现后统一接入。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.domains.auth.router import admin_router as auth_admin_router
from app.domains.auth.router import router as auth_router
from app.domains.agent.router import router as agent_router
from app.domains.artifact.router import router as artifact_router
from app.domains.chat.router import router as chat_router
from app.domains.discover.router import router as discover_router
from app.domains.gap.router import router as gap_router
from app.domains.knowledge.router import router as knowledge_router
from app.domains.paper.router import router as paper_router
from app.domains.reading.router import router as reading_router
from app.domains.recommendation.router import router as recommendation_router
from app.domains.retrieval.router import router as retrieval_router
from app.domains.task.router import router as task_router
from app.domains.timeline.router import router as timeline_router
from app.domains.workspace.router import router as workspace_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(auth_admin_router)
api_router.include_router(workspace_router)
api_router.include_router(paper_router)
api_router.include_router(artifact_router)
api_router.include_router(task_router)
api_router.include_router(timeline_router)
api_router.include_router(knowledge_router)
api_router.include_router(retrieval_router)
api_router.include_router(reading_router)
api_router.include_router(recommendation_router)
api_router.include_router(discover_router)
api_router.include_router(chat_router)
api_router.include_router(agent_router)
api_router.include_router(gap_router)
