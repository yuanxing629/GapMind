"""SQLAlchemy declarative base.

All domain models inherit from `Base` so Alembic can autodetect them via the
`app.db.models` import in `alembic/env.py`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator
from sqlalchemy import String as _String


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class UUIDString(TypeDecorator):
    """Store UUIDs as 36-char strings for cross-DB portability.

    PostgreSQL has a native UUID type, but using strings keeps the schema
    portable and avoids dialect-specific issues during MVP. Both bind and
    result values are strings - ORM models declare these columns as
    `Mapped[str]` and Pydantic schemas treat them as `str`.
    """

    impl = _String(36)
    cache_ok = True

    def process_bind_param(self, value: Any | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, UUID):
            return str(value)
        # Accept strings that may or may not be valid UUIDs; normalize if valid.
        try:
            return str(UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            return str(value)

    def process_result_value(self, value: Any | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return str(value)


class TimestampMixin:
    """Common created_at / updated_at columns."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UUIDPKMixin:
    """UUID primary key column named `id`."""

    id: Mapped[str] = mapped_column(
        UUIDString(),
        primary_key=True,
        default=lambda: str(uuid4()),
        nullable=False,
    )
