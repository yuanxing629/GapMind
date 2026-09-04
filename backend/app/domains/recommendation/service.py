"""生成并持久化可解释的论文推荐。"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domains.paper.models import Paper
from app.domains.paper.schemas import SemanticScholarPaper
from app.domains.recommendation.models import PaperRecommendation
from app.domains.workspace.models import Workspace
from app.gateway.semantic_scholar import SemanticScholarClient

RECOMMENDATION_TTL = timedelta(hours=24)
MAX_PROFILE_TOPICS = 8
MAX_CANDIDATES_PER_QUERY = 20
S2_SEARCH_FIELDS = (
    "paperId,corpusId,externalIds,title,abstract,year,publicationDate,authors,"
    "venue,url,citationCount,referenceCount,influentialCitationCount,isOpenAccess,"
    "openAccessPdf,fieldsOfStudy,s2FieldsOfStudy,publicationTypes"
)


class RecommendationNotFoundError(Exception):
    def __init__(self, external_paper_id: str) -> None:
        super().__init__(f"Recommendation not found: {external_paper_id}")
        self.external_paper_id = external_paper_id


class RecommendationService:
    """MVP 的 workspace 推荐缓存与排序逻辑。"""

    def __init__(self, db: Session) -> None:
        self.db = db

    def current(self, workspace_id: str) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        topics, has_profile = self._profile(workspace)
        all_rows = list(
            self.db.execute(
                select(PaperRecommendation.generated_at).where(
                    PaperRecommendation.workspace_id == workspace_id
                )
            ).scalars().all()
        )
        rows = list(
            self.db.execute(
                select(PaperRecommendation)
                .where(
                    PaperRecommendation.workspace_id == workspace_id,
                    PaperRecommendation.is_active.is_(True),
                    PaperRecommendation.status != "dismissed",
                )
                .order_by(PaperRecommendation.score.desc(), PaperRecommendation.generated_at.desc())
            ).scalars().all()
        )
        generated_at = max(all_rows, default=None)
        return {
            "workspace_id": workspace_id,
            "profile_topics": topics,
            "has_profile": has_profile,
            "generated_at": generated_at,
            "stale": self._is_stale(generated_at),
            "items": rows,
        }

    def needs_generation(self, workspace_id: str) -> bool:
        cached = self.db.execute(
            select(PaperRecommendation.id)
            .where(PaperRecommendation.workspace_id == workspace_id)
            .limit(1)
        ).first()
        return cached is None

    def refresh(
        self, workspace_id: str, client: SemanticScholarClient
    ) -> dict[str, Any]:
        workspace = self._workspace(workspace_id)
        topics, has_profile = self._profile(workspace)
        queries = self._queries(workspace, topics)
        existing_papers = list(
            self.db.execute(
                select(Paper).where(
                    Paper.workspace_id == workspace_id,
                    Paper.is_deleted.is_(False),
                )
            ).scalars().all()
        )
        excluded = self._existing_keys(existing_papers)

        candidates: dict[str, tuple[dict[str, Any], int]] = {}
        for query_index, query in enumerate(queries):
            raw = client.search(
                query=query,
                fields=S2_SEARCH_FIELDS,
                sort="relevance",
                limit=MAX_CANDIDATES_PER_QUERY,
                offset=0,
            )
            for rank, item in enumerate(raw.get("data") or []):
                if not isinstance(item, dict):
                    continue
                paper = SemanticScholarPaper.model_validate(item)
                paper_id = paper.paper_id.strip()
                if not paper_id or self._paper_keys(paper) & excluded:
                    continue
                if paper_id not in candidates:
                    candidates[paper_id] = (paper.model_dump(by_alias=True, mode="json"), rank + query_index * MAX_CANDIDATES_PER_QUERY)

        ranked: list[tuple[float, dict[str, Any], list[str], list[str]]] = []
        max_citations = max(
            (int((item.get("citationCount") or 0)) for item, _ in candidates.values()),
            default=1,
        )
        for paper_dict, rank in candidates.values():
            paper = SemanticScholarPaper.model_validate(paper_dict)
            matched_topics = self._matched_topics(paper, topics)
            lexical = self._lexical_score(paper, topics)
            relevance = max(0.0, 1.0 - min(rank, MAX_CANDIDATES_PER_QUERY * 3) / (MAX_CANDIDATES_PER_QUERY * 3))
            year = paper.year or 0
            freshness = max(0.0, min(1.0, (year - 2018) / 8)) if year else 0.0
            citations = int(paper.citation_count or 0)
            authority = min(1.0, math.log1p(citations) / max(1.0, math.log1p(max_citations)))
            available = 1.0 if paper.is_open_access or (paper.open_access_pdf or {}).get("url") else 0.0
            score = 0.45 * lexical + 0.20 * relevance + 0.15 * freshness + 0.10 * authority + 0.10 * available
            reasons = self._reasons(
                matched_topics=matched_topics,
                paper=paper,
                freshness=freshness,
                available=available,
            )
            ranked.append((score, paper_dict, reasons, matched_topics))

        ranked.sort(key=lambda value: value[0], reverse=True)
        selected = self._diversify(ranked, limit=20)
        now = datetime.now(UTC)
        self.db.execute(
            update(PaperRecommendation)
            .where(PaperRecommendation.workspace_id == workspace_id)
            .values(is_active=False)
        )
        for score, paper_dict, reasons, matched_topics in selected:
            external_id = str(paper_dict["paperId"])
            row = self.db.execute(
                select(PaperRecommendation).where(
                    PaperRecommendation.workspace_id == workspace_id,
                    PaperRecommendation.external_paper_id == external_id,
                )
            ).scalar_one_or_none()
            if row is None:
                row = PaperRecommendation(
                    id=str(uuid4()),
                    workspace_id=workspace_id,
                    external_paper_id=external_id,
                )
                self.db.add(row)
            row.paper = paper_dict
            row.score = round(score, 6)
            row.reasons = reasons
            row.topics = matched_topics
            row.generated_at = now
            if row.status == "dismissed":
                row.is_active = False
            else:
                row.status = "suggested"
                row.is_active = True
        self.db.commit()
        return self.current(workspace_id)

    def feedback(
        self,
        workspace_id: str,
        external_paper_id: str,
        action: str,
    ) -> PaperRecommendation:
        row = self.db.execute(
            select(PaperRecommendation).where(
                PaperRecommendation.workspace_id == workspace_id,
                PaperRecommendation.external_paper_id == external_paper_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise RecommendationNotFoundError(external_paper_id)
        if action == "dismiss":
            row.status = "dismissed"
            row.is_active = False
        elif action == "restore":
            row.status = "suggested"
            row.is_active = True
        else:
            row.status = action
            row.is_active = True
        self.db.commit()
        self.db.refresh(row)
        return row

    def _workspace(self, workspace_id: str) -> Workspace:
        workspace = self.db.get(Workspace, workspace_id)
        if workspace is None or workspace.is_deleted:
            from app.domains.workspace.service import WorkspaceNotFoundError

            raise WorkspaceNotFoundError(workspace_id)
        return workspace

    @staticmethod
    def _profile(workspace: Workspace) -> tuple[list[str], bool]:
        values: list[str] = []
        for value in [workspace.topic]:
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        for collection in [workspace.keywords or [], workspace.active_questions or []]:
            for value in collection:
                if isinstance(value, str) and value.strip():
                    values.append(value.strip())
        has_profile = bool(values or workspace.goals or workspace.description)
        if not values:
            fallback = workspace.goals or workspace.description or workspace.name
            values.append(str(fallback).strip()[:160])
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.casefold()
            if normalized not in seen:
                unique.append(value)
                seen.add(normalized)
        return unique[:MAX_PROFILE_TOPICS], has_profile

    @staticmethod
    def _queries(workspace: Workspace, topics: list[str]) -> list[str]:
        keywords = [value.strip() for value in (workspace.keywords or []) if isinstance(value, str) and value.strip()]
        candidates = [" ".join(topics[:3])]
        if workspace.topic:
            candidates.append(workspace.topic)
        if keywords:
            candidates.append(" ".join(keywords[:4]))
        if workspace.active_questions:
            candidates.append(str(workspace.active_questions[0]))
        if workspace.goals:
            candidates.append(workspace.goals)
        if workspace.description:
            candidates.append(workspace.description)
        seen: set[str] = set()
        queries: list[str] = []
        for value in candidates:
            query = re.sub(r"\s+", " ", value).strip()
            if query and query.casefold() not in seen:
                queries.append(query[:200])
                seen.add(query.casefold())
        return queries or [workspace.name[:200]]

    @staticmethod
    def _tokens(value: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9][a-z0-9+#._-]{1,}|[\u4e00-\u9fff]{2,}", value.casefold())
            if token not in {"the", "and", "for", "with", "from", "using", "研究", "方法"}
        }

    @classmethod
    def _paper_text(cls, paper: SemanticScholarPaper) -> str:
        fields = [paper.title or "", paper.abstract or "", paper.venue or ""]
        fields.extend(paper.fields_of_study or [])
        return " ".join(fields)

    @classmethod
    def _lexical_score(cls, paper: SemanticScholarPaper, topics: list[str]) -> float:
        paper_tokens = cls._tokens(cls._paper_text(paper))
        profile_tokens = cls._tokens(" ".join(topics))
        if not profile_tokens:
            return 0.0
        return min(1.0, len(paper_tokens & profile_tokens) / len(profile_tokens))

    @classmethod
    def _matched_topics(cls, paper: SemanticScholarPaper, topics: list[str]) -> list[str]:
        paper_tokens = cls._tokens(cls._paper_text(paper))
        return [topic for topic in topics if cls._tokens(topic) & paper_tokens][:3]

    @staticmethod
    def _reasons(
        *, matched_topics: list[str], paper: SemanticScholarPaper, freshness: float, available: float
    ) -> list[str]:
        reasons: list[str] = []
        if matched_topics:
            reasons.append(f"与你关注的“{matched_topics[0]}”方向相关")
        if freshness >= 0.75:
            reasons.append("属于近年的研究成果")
        if available:
            reasons.append("存在可访问的开放 PDF")
        if not reasons:
            reasons.append("与当前课题主题相关")
        if paper.citation_count:
            reasons.append(f"Semantic Scholar 引用 {paper.citation_count} 次")
        return reasons[:3]

    @classmethod
    def _diversify(
        cls, ranked: list[tuple[float, dict[str, Any], list[str], list[str]]], limit: int
    ) -> list[tuple[float, dict[str, Any], list[str], list[str]]]:
        selected: list[tuple[float, dict[str, Any], list[str], list[str]]] = []
        seen_titles: set[str] = set()
        for candidate in ranked:
            title = re.sub(r"\W+", " ", str(candidate[1].get("title") or "").casefold()).strip()
            if title and title in seen_titles:
                continue
            selected.append(candidate)
            if title:
                seen_titles.add(title)
            if len(selected) >= limit:
                break
        return selected

    @staticmethod
    def _existing_keys(papers: list[Paper]) -> set[str]:
        keys: set[str] = set()
        for paper in papers:
            if paper.external_paper_id:
                keys.add(paper.external_paper_id.casefold())
            if paper.doi:
                keys.add(paper.doi.casefold())
            if paper.arxiv_id:
                keys.add(paper.arxiv_id.casefold())
            keys.add(RecommendationService._normalize_title(paper.title))
        return keys

    @staticmethod
    def _paper_keys(paper: SemanticScholarPaper) -> set[str]:
        keys = {paper.paper_id.casefold(), RecommendationService._normalize_title(paper.title or "")}
        external_ids = paper.external_ids or {}
        for name in ("DOI", "ArXiv", "ARXIV"):
            value = external_ids.get(name)
            if isinstance(value, str) and value.strip():
                keys.add(value.casefold())
        return keys

    @staticmethod
    def _normalize_title(value: str) -> str:
        return re.sub(r"\W+", " ", value.casefold()).strip()

    @staticmethod
    def _is_stale(value: datetime | None) -> bool:
        if value is None:
            return True
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return datetime.now(UTC) - value > RECOMMENDATION_TTL
