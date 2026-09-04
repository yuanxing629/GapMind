"""外部论文搜索的持久化历史与收藏模型。"""

from __future__ import annotations

from sqlalchemy import JSON, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class PaperSearchHistory(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "paper_search_histories"

    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, default="user", index=True)
    query: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    filters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    sort: Mapped[str] = mapped_column(String(64), nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PaperSearchFavorite(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "paper_search_favorites"

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "semantic_scholar_paper_id",
            name="uq_paper_search_favorite_owner_paper",
        ),
    )

    owner_id: Mapped[str] = mapped_column(String(128), nullable=False, default="user", index=True)
    semantic_scholar_paper_id: Mapped[str] = mapped_column(
        String(255), nullable=False, index=True
    )
    paper: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
