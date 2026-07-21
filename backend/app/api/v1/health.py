"""Health check endpoints.

Phase 0: basic liveness + readiness. Phase 1+ will add DB, Redis, Milvus,
LLM/Embedding provider connectivity checks.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.gateway.embedding import get_embedding_gateway
from app.gateway.llm import get_llm_gateway

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
@router.get("/")
def health() -> dict[str, str]:
    """Liveness check - always 200 if the process is up."""
    return {"status": "ok", "env": settings.app_env}


@router.get("/ready")
def readiness() -> dict[str, object]:
    """Readiness check - probes configured external services.

    Phase 0: only checks that LLM/Embedding API keys are set. DB/Redis/Milvus
    connectivity will be added in Phase 1-2.
    """
    llm = get_llm_gateway()
    emb = get_embedding_gateway()
    return {
        "status": "ok",
        "checks": {
            "llm_key": "ok" if llm.ping() else "missing",
            "embedding_key": "ok" if emb.ping() else "missing",
        },
    }
