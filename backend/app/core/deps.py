"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


# Session factory is created lazily in db.session; re-exported here for deps.
# Importing here would create a circular import, so we import inside the function.
def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy session."""
    from app.db.session import SessionLocal

    session_factory: sessionmaker[Session] = SessionLocal
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def get_settings_dep() -> "settings.__class__":  # type: ignore[valid-type]
    """FastAPI dependency returning the cached Settings instance."""
    return settings
