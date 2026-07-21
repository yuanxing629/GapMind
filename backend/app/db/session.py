"""SQLAlchemy session factory.

Kept separate from `base.py` to avoid circular imports during model loading.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker, declarative_base

from app.core.config import settings


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    echo=settings.is_dev and False,  # set True for SQL logging during debugging
)

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """Context manager that commits on success, rolls back on exception."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
