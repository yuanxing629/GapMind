"""工作区论文推荐的 API schemas。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domains.paper.schemas import SemanticScholarPaper


class PaperRecommendationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    external_paper_id: str
    paper: SemanticScholarPaper
    score: float
    reasons: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    status: str = "suggested"
    generated_at: datetime


class PaperRecommendationListRead(BaseModel):
    workspace_id: str
    profile_topics: list[str] = Field(default_factory=list)
    has_profile: bool = False
    generated_at: datetime | None = None
    stale: bool = False
    items: list[PaperRecommendationRead] = Field(default_factory=list)


class PaperRecommendationFeedback(BaseModel):
    action: Literal["open", "favorite", "imported", "reading", "dismiss", "restore"]

