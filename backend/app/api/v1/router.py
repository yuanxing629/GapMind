"""API v1 router aggregator.

Domain routers will be included here as they are implemented.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.health import router as health_router
from app.domains.artifact.router import router as artifact_router
from app.domains.knowledge.router import router as knowledge_router
from app.domains.paper.router import router as paper_router
from app.domains.task.router import router as task_router
from app.domains.timeline.router import router as timeline_router
from app.domains.workspace.router import router as workspace_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(workspace_router)
api_router.include_router(paper_router)
api_router.include_router(artifact_router)
api_router.include_router(task_router)
api_router.include_router(timeline_router)
api_router.include_router(knowledge_router)
