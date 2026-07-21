"""FastAPI application entry point.

Phase 0: app skeleton with health check + CORS + logging. Domain routers
land in Phase 1+.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import SessionLocal  # noqa: F401  (ensures engine is created at import)


logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    logger.info(
        "app.startup",
        env=settings.app_env,
        host=settings.app_host,
        port=settings.app_port,
    )
    yield
    logger.info("app.shutdown")


app = FastAPI(
    title="GapMind API",
    description="Evidence-grounded, Human-in-the-Loop AI Research Workspace",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/")
def root() -> dict[str, str]:
    """Root redirect hint - real API lives under /api/v1."""
    return {"name": "GapMind API", "docs": "/docs", "openapi": "/openapi.json"}
