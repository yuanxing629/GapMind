"""Test configuration.

Provides a SQLite-based engine for unit tests (fast, no external DB needed)
and an override for the `get_db` dependency so each test gets an isolated
in-memory session.

Also patches `spawn_parse_pdf_task` to run the parser synchronously in the
same test session, so Phase 2 tests can verify the full pipeline without
a real Celery worker / Redis.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.deps import get_db
from app.db.base import Base
from app.db.models import *  # noqa: F401,F403  (registers all models on Base.metadata)
from app.main import app


@pytest.fixture(scope="function")
def db_session() -> Generator[Session, None, None]:
    """Yield a fresh in-memory SQLite session for each test.

    StaticPool + check_same_thread=False lets the TestClient (which may run
    in a different thread) share the same in-memory connection.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    connection = engine.connect()
    transaction = connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()
    engine.dispose()


@pytest.fixture(scope="function")
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient with the get_db dependency overridden to use the test session.

    Also patches spawn_parse_pdf_task to run the parser synchronously so
    tests can verify the full Phase 2 pipeline without Celery/Redis.
    """

    def _override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            # The test session is closed by the db_session fixture; nothing to do here.
            pass

    app.dependency_overrides[get_db] = _override_get_db

    # Patch spawn_parse_pdf_task so it runs synchronously in the test session
    # instead of dispatching to Celery. The paper service imports this name
    # lazily inside its methods, so patching the module attribute is enough.
    with patch(
        "app.workers.tasks.parse_pdf.spawn_parse_pdf_task",
        new=_sync_spawn_parse_pdf,
    ):
        with TestClient(app) as c:
            yield c

    app.dependency_overrides.clear()


def _sync_spawn_parse_pdf(db: Session, paper_id: str, workspace_id: str) -> str:
    """Test replacement for spawn_parse_pdf_task.

    Creates a Task row (so the API/UI can show it) and runs the parser
    synchronously in the same DB session, so the test sees the final
    state immediately.
    """
    from app.domains.task.schemas import TaskCreate
    from app.domains.task.service import TaskService
    from app.workers.tasks.parse_pdf import _run_parse_pdf

    task = TaskService(db).create(
        TaskCreate(
            workspace_id=workspace_id,
            task_type="parse_pdf",
            payload={"paper_id": paper_id},
        )
    )
    # Run synchronously in the test session.
    _run_parse_pdf(db, task.id)
    return task.id
