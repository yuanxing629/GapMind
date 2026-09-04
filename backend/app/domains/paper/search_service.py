"""论文搜索历史与收藏的数据库操作。"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domains.paper.search_models import PaperSearchFavorite, PaperSearchHistory


class PaperSearchService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record_history(
        self,
        *,
        query: str,
        filters: dict,
        sort: str,
        result_count: int,
        actor_id: str | None = None,
    ) -> PaperSearchHistory:
        row = PaperSearchHistory(
            owner_id=actor_id or "user",
            query=query,
            filters=filters,
            sort=sort,
            result_count=result_count,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    def list_history(
        self, *, limit: int, offset: int, actor_id: str | None = None
    ) -> list[PaperSearchHistory]:
        query = select(PaperSearchHistory)
        if actor_id is not None:
            query = query.where(PaperSearchHistory.owner_id == actor_id)
        query = query.order_by(PaperSearchHistory.created_at.desc()).limit(limit).offset(offset)
        return list(self.db.execute(query).scalars().all())

    def delete_history(self, history_id: str, *, actor_id: str | None = None) -> bool:
        stmt = delete(PaperSearchHistory).where(PaperSearchHistory.id == history_id)
        if actor_id is not None:
            stmt = stmt.where(PaperSearchHistory.owner_id == actor_id)
        result = self.db.execute(stmt)
        self.db.commit()
        return bool(result.rowcount)

    def upsert_favorite(
        self, *, paper: dict, note: str | None, actor_id: str | None = None
    ) -> PaperSearchFavorite:
        paper_id = str(paper.get("paperId") or "").strip()
        if not paper_id:
            raise ValueError("paper.paperId is required")
        owner_id = actor_id or "user"
        row = self.db.execute(
            select(PaperSearchFavorite).where(
                PaperSearchFavorite.semantic_scholar_paper_id == paper_id,
                PaperSearchFavorite.owner_id == owner_id,
            )
        ).scalar_one_or_none()
        if row is None:
            row = PaperSearchFavorite(
                owner_id=owner_id,
                semantic_scholar_paper_id=paper_id,
                paper=paper,
                note=note,
            )
            self.db.add(row)
        else:
            row.paper = paper
            row.note = note
        self.db.commit()
        self.db.refresh(row)
        return row

    def list_favorites(
        self, *, limit: int, offset: int, actor_id: str | None = None
    ) -> list[PaperSearchFavorite]:
        query = select(PaperSearchFavorite)
        if actor_id is not None:
            query = query.where(PaperSearchFavorite.owner_id == actor_id)
        query = query.order_by(PaperSearchFavorite.created_at.desc()).limit(limit).offset(offset)
        return list(self.db.execute(query).scalars().all())

    def delete_favorite(self, paper_id: str, *, actor_id: str | None = None) -> bool:
        stmt = delete(PaperSearchFavorite).where(
            PaperSearchFavorite.semantic_scholar_paper_id == paper_id
        )
        if actor_id is not None:
            stmt = stmt.where(PaperSearchFavorite.owner_id == actor_id)
        result = self.db.execute(stmt)
        self.db.commit()
        return bool(result.rowcount)
