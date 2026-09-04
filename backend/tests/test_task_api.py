"""Task 状态机和 Timeline 的集成测试。

Phase 1b 不通过 HTTP 暴露 task creation（task 由 Phase 2 的系统流程派发）。测试通过 service
layer 创建 task，并使用 TestClient 看到的同一个 `db_session`：将 `get_db` dependency
覆盖为返回该 session 的函数，再用小型 helper 以该 session 构造 TaskService。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.domains.task.schemas import TaskCreate
from app.domains.task.service import TaskService


@pytest.fixture
def task_factory(db_session: Session):
    """返回一个使用测试 session 创建任务的可调用对象。"""

    def _make(workspace_id: str | None, task_type: str = "parse_pdf") -> str:
        svc = TaskService(db_session)
        task = svc.create(TaskCreate(workspace_id=workspace_id, task_type=task_type))
        return task.id

    return _make


@pytest.fixture
def task_transitioner(db_session: Session):
    """返回一个使用测试 session 转换任务状态的可调用对象。"""

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
# Cancel 会立即完成（之前会停留在永远不会推进的
    # "cancel_requested" state, leaving the UI stuck on "正在取消").
    assert resp.json()["status"] == "cancelled"
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
    client: TestClient, task_factory, task_transitioner, monkeypatch
) -> None:
    ws = _create_workspace(client)
    tid = task_factory(ws["id"])

    task_transitioner(tid, "running")
    task_transitioner(tid, "failed", error="boom")

# Hermetic：替换 Celery 重新派发（retry 端点现在会重新入队 Celery 任务，
# 使 queued 行真正得到处理）。
    import app.workers.tasks.dispatch as dispatch_mod

    monkeypatch.setattr(dispatch_mod, "redispatch_task", lambda task: "fake-celery-id")

    resp = client.post(f"/api/v1/tasks/{tid}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["error"] is None
# 重新派发的 Celery id 会记录在该行中。
    assert client.get(f"/api/v1/tasks/{tid}").json()["celery_task_id"] == "fake-celery-id"


def test_task_retry_reports_redispatch_failure_instead_of_staying_queued(
    client: TestClient, task_factory, task_transitioner, monkeypatch
) -> None:
    ws = _create_workspace(client)
    tid = task_factory(ws["id"])
    task_transitioner(tid, "running")
    task_transitioner(tid, "failed", error="old failure")

    import app.workers.tasks.dispatch as dispatch_mod

    monkeypatch.setattr(
        dispatch_mod,
        "redispatch_task",
        lambda task: (_ for _ in ()).throw(RuntimeError("broker offline")),
    )

    resp = client.post(f"/api/v1/tasks/{tid}/retry")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert "重新派发失败" in body["error"]
    assert "broker offline" in body["error"]


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


def test_task_resource_routes_are_owner_scoped(
    client: TestClient, task_factory
) -> None:
    alice = {"X-User-ID": "alice"}
    bob = {"X-User-ID": "bob"}
    ws = client.post(
        "/api/v1/workspaces", headers=alice, json={"name": "Alice Task WS"}
    ).json()
    tid = task_factory(ws["id"])

    assert client.get(f"/api/v1/tasks/{tid}", headers=bob).status_code == 404
    assert client.post(f"/api/v1/tasks/{tid}/cancel", headers=bob).status_code == 404


def test_unscoped_task_resource_is_hidden_from_user_api(client: TestClient, task_factory) -> None:
    tid = task_factory(None)

    assert client.get(f"/api/v1/tasks/{tid}").status_code == 404
    assert client.post(f"/api/v1/tasks/{tid}/cancel").status_code == 404
