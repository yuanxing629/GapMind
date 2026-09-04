"""SQLAlchemy 声明式基类。

所有领域模型都继承 `Base`，使 Alembic 可以通过
通过在 `alembic/env.py` 导入 `app.db.models` 完成自动发现。
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
    """所有 ORM 模型的 declarative base。"""


class UUIDString(TypeDecorator):
    """将 UUID 以 36 字符字符串保存，保证跨数据库可移植性。

    PostgreSQL 原生支持 UUID 类型，但使用字符串可以保持 schema 可移植，并避免 MVP 阶段的方言差异。
    绑定值和结果值都是字符串，ORM 模型将这些列声明为 `Mapped[str]`，Pydantic schema 将其视为 `str`。
    """

    impl = _String(36)
    cache_ok = True

    def process_bind_param(self, value: Any | None, dialect: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, UUID):
            return str(value)
# 接受可能有效或无效的 UUID 字符串；有效时进行标准化。
        try:
            return str(UUID(str(value)))
        except (ValueError, AttributeError, TypeError):
            return str(value)

    def process_result_value(self, value: Any | None, dialect: Any) -> str | None:
        if value is None:
            return None
        return str(value)


class TimestampMixin:
    """共用的 created_at / updated_at 字段。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class UUIDPKMixin:
    """名为 `id` 的 UUID 主键字段。"""

    id: Mapped[str] = mapped_column(
        UUIDString(),
        primary_key=True,
        default=lambda: str(uuid4()),
        nullable=False,
    )
