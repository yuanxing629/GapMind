"""Smoke tests for Knowledge read-only API (Phase 1b).

Knowledge content is written by the extraction pipeline in Phase 3, so
Phase 1b only verifies that the endpoints respond with empty lists and
that workspace scoping works.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_workspace(client: TestClient, name: str = "WS") -> dict:
    resp = client.post("/api/v1/workspaces", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_list_knowledge_empty(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.get(f"/api/v1/workspaces/{ws['id']}/knowledge")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_relations_empty(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.get(f"/api/v1/workspaces/{ws['id']}/knowledge/relations")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_get_knowledge_item_not_found(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "knowledge_item_not_found"


def test_knowledge_workspace_not_found(client: TestClient) -> None:
    resp = client.get("/api/v1/workspaces/00000000-0000-0000-0000-000000000000/knowledge")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"
