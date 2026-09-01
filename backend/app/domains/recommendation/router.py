"""HTTP API for workspace-scoped paper recommendations."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.deps import get_db, get_owned_workspace
from app.domains.recommendation.schemas import (
    PaperRecommendationFeedback,
    PaperRecommendationListRead,
    PaperRecommendationRead,
)
from app.domains.recommendation.service import (
    RecommendationNotFoundError,
    RecommendationService,
)
from app.gateway.semantic_scholar import SemanticScholarClient

router = APIRouter(
    prefix="/workspaces",
    tags=["recommendation"],
    dependencies=[Depends(get_owned_workspace)],
)


def _service(db: Session = Depends(get_db)) -> RecommendationService:  # noqa: B008
    return RecommendationService(db)


def _client() -> SemanticScholarClient:
    return SemanticScholarClient()


def _read(payload: dict) -> PaperRecommendationListRead:
    return PaperRecommendationListRead(
        workspace_id=payload["workspace_id"],
        profile_topics=payload["profile_topics"],
        has_profile=payload["has_profile"],
        generated_at=payload["generated_at"],
        stale=payload["stale"],
        items=[PaperRecommendationRead.model_validate(row) for row in payload["items"]],
    )


@router.get(
    "/{workspace_id}/recommendations",
    response_model=PaperRecommendationListRead,
)
def list_recommendations(
    workspace_id: str,
    service: RecommendationService = Depends(_service),  # noqa: B008
    client: SemanticScholarClient = Depends(_client),  # noqa: B008
) -> PaperRecommendationListRead:
    """Return cached recommendations, generating the first batch on demand."""
    if service.needs_generation(workspace_id):
        return _read(service.refresh(workspace_id, client))
    return _read(service.current(workspace_id))


@router.post(
    "/{workspace_id}/recommendations/refresh",
    response_model=PaperRecommendationListRead,
)
def refresh_recommendations(
    workspace_id: str,
    service: RecommendationService = Depends(_service),  # noqa: B008
    client: SemanticScholarClient = Depends(_client),  # noqa: B008
) -> PaperRecommendationListRead:
    return _read(service.refresh(workspace_id, client))


@router.post(
    "/{workspace_id}/recommendations/{external_paper_id}/feedback",
    response_model=PaperRecommendationRead,
)
def recommendation_feedback(
    workspace_id: str,
    external_paper_id: str,
    payload: PaperRecommendationFeedback,
    service: RecommendationService = Depends(_service),  # noqa: B008
) -> PaperRecommendationRead:
    try:
        row = service.feedback(workspace_id, external_paper_id, payload.action)
    except RecommendationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "recommendation_not_found", "message": str(exc)},
        ) from exc
    return PaperRecommendationRead.model_validate(row)
