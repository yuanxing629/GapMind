"""Integration tests for the Task state machine + Timeline.

Task creation isn't exposed via HTTP in Phase 1b (tasks are spawned by the
system in Phase 2). For tests we create tasks through the service layer
using the same `db_session` the TestClient sees, by overriding the
`get_db` dependency to a function that returns our session and using a
small helper that constructs TaskService with that session.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domains.task.schemas import TaskCreate
from app.domains.task.service import TaskService


@pytest.fixture
def task_factory(db_session: Session):
    """Return a callable that creates a task using the test session."""

    def _make(workspace_id: str, task_type: str = "parse_pdf") -> str:
        svc = TaskService(db_session)
        task = svc.create(TaskCreate(workspace_id=workspace_id, task_type=task_type))
        return task.id

    return _make


@pytest.fixture
def task_transitioner(db_session: Session):
    """Return a callable that transitions a task using the test session."""

    def _transition(task_id: str, to_status: str, **kwargs) -> None:
        TaskService(db_session).transition(task_id, to_status, **kwargs)

    return _transition


def _create_workspace(client: TestClient, name: str = "WS") -> dict:
    resp = client.post("/api/v1/workspaces", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_task_cancel_from_queued(
    client: TestClient, task_factory, task_transitioner
) -> None:
    ws = _create_workspace(client)
    tid = task_factory(ws["id"], "parse_pdf")

    resp = client.post(f"/api/v1/tasks/{tid}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancel_requested"

    # Worker honors cancel -> cancelled.
    task_transitioner(tid, "cancelled")
    assert client.get(f"/api/v1/tasks/{tid}").json()["status"] == "cancelled"


def test_task_cancel_from_terminal_returns_409(
    client: TestClient, task_factory, task_transitioner
) -> None:
    ws = _create_workspace(client)
    tid = task_factory(ws["id"])

    task_transitioner(tid, "running")
    task_transitioner(tid, "succeeded")

    resp = client.post(f"/api/v1/tasks/{tid}/cancel")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "invalid_task_transition"


def test_task_retry_from_failed(
    client: TestClient, task_factory, task_transitioner
) -> None:
    ws = _create_workspace(client)
    tid = task_factory(ws["id"])

    task_transitioner(tid, "running")
    task_transitioner(tid, "failed", error="boom")

    resp = client.post(f"/api/v1/tasks/{tid}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["error"] is None


def test_task_retry_from_non_failed_returns_409(
    client: TestClient, task_factory
) -> None:
    ws = _create_workspace(client)
    tid = task_factory(ws["id"])  # status is "queued"
    resp = client.post(f"/api/v1/tasks/{tid}/retry")
    assert resp.status_code == 409


def test_task_list_filtered_by_status(
    client: TestClient, task_factory, task_transitioner
) -> None:
    ws = _create_workspace(client)
    t1 = task_factory(ws["id"], "parse_pdf")
    t2 = task_factory(ws["id"], "embed_chunks")

    task_transitioner(t1, "running")
    task_transitioner(t1, "succeeded")

    body = client.get(
        f"/api/v1/workspaces/{ws['id']}/tasks", params={"status": "succeeded"}
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == t1

    body = client.get(
        f"/api/v1/workspaces/{ws['id']}/tasks", params={"status": "queued"}
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == t2


def test_task_timeline_events_recorded(
    client: TestClient, task_factory, task_transitioner
) -> None:
    ws = _create_workspace(client)
    tid = task_factory(ws["id"])

    task_transitioner(tid, "running")
    task_transitioner(tid, "succeeded")

    timeline = client.get(f"/api/v1/workspaces/{ws['id']}/timeline").json()
    types = [e["event_type"] for e in timeline["items"]]
    assert "task.created" in types
    assert "task.running" in types
    assert "task.succeeded" in types


def test_task_get_not_found(client: TestClient) -> None:
    resp = client.get("/api/v1/tasks/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
