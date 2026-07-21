"""Integration tests for the Workspace HTTP API."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_workspace_minimal(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/workspaces",
        json={"name": "Self-Interpretable GNN"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Self-Interpretable GNN"
    assert body["keywords"] == []
    assert body["active_questions"] == []
    assert body["is_archived"] is False
    assert body["is_deleted"] is False
    assert "id" in body
    assert "created_at" in body


def test_create_workspace_full_profile(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/workspaces",
        json={
            "name": "  GNN Explainability  ",
            "description": "Survey and opportunity discovery for self-interpretable GNNs.",
            "topic": "Self-Interpretable Graph Neural Networks",
            "keywords": ["  GNN ", " explainability ", "  "],  # whitespace + empties
            "goals": "Find a research gap with multi-paper evidence support.",
            "constraints": "Compute budget: 1 A100. No proprietary datasets.",
            "active_questions": ["Why do post-hoc explainers fail on GNNs?"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "GNN Explainability"  # stripped
    assert body["keywords"] == ["GNN", "explainability"]  # stripped + empties filtered
    assert body["active_questions"] == ["Why do post-hoc explainers fail on GNNs?"]


def test_create_workspace_rejects_empty_name(client: TestClient) -> None:
    resp = client.post("/api/v1/workspaces", json={"name": "   "})
    assert resp.status_code == 422


def test_create_workspace_rejects_missing_name(client: TestClient) -> None:
    resp = client.post("/api/v1/workspaces", json={"topic": "no name"})
    assert resp.status_code == 422


def test_get_workspace(client: TestClient) -> None:
    created = client.post("/api/v1/workspaces", json={"name": "WS-A"}).json()
    resp = client.get(f"/api/v1/workspaces/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


def test_get_workspace_not_found(client: TestClient) -> None:
    resp = client.get("/api/v1/workspaces/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"]["error"] == "workspace_not_found"


def test_get_workspace_invalid_uuid_returns_404(client: TestClient) -> None:
    # Non-UUID strings are treated as not-found (avoid leaking validation internals).
    resp = client.get("/api/v1/workspaces/not-a-uuid")
    assert resp.status_code == 404


def test_list_workspaces_excludes_archived_and_deleted(client: TestClient) -> None:
    a = client.post("/api/v1/workspaces", json={"name": "A"}).json()
    b = client.post("/api/v1/workspaces", json={"name": "B"}).json()
    c = client.post("/api/v1/workspaces", json={"name": "C"}).json()

    client.post(f"/api/v1/workspaces/{b['id']}/archive")
    client.delete(f"/api/v1/workspaces/{c['id']}")

    resp = client.get("/api/v1/workspaces")
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {a["id"]}
    assert body["total"] == 1

    # include_archived returns A and B (but not soft-deleted C)
    resp = client.get("/api/v1/workspaces?include_archived=true")
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {a["id"], b["id"]}


def test_list_workspaces_pagination(client: TestClient) -> None:
    for i in range(5):
        client.post("/api/v1/workspaces", json={"name": f"WS-{i}"})

    resp = client.get("/api/v1/workspaces?limit=2&offset=0")
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5
    assert body["limit"] == 2
    assert body["offset"] == 0

    resp = client.get("/api/v1/workspaces?limit=2&offset=2")
    assert len(resp.json()["items"]) == 2


def test_update_workspace_partial(client: TestClient) -> None:
    created = client.post(
        "/api/v1/workspaces", json={"name": "Old", "topic": "old topic"}
    ).json()

    resp = client.patch(
        f"/api/v1/workspaces/{created['id']}",
        json={"name": "New Name", "keywords": ["a", "b"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New Name"
    assert body["keywords"] == ["a", "b"]
    # Unspecified fields are preserved (exclude_unset=True at the schema level).
    assert body["topic"] == "old topic"


def test_update_workspace_empty_payload_is_noop(client: TestClient) -> None:
    created = client.post("/api/v1/workspaces", json={"name": "X"}).json()
    resp = client.patch(f"/api/v1/workspaces/{created['id']}", json={})
    assert resp.status_code == 200
    assert resp.json()["name"] == "X"


def test_update_workspace_not_found(client: TestClient) -> None:
    resp = client.patch(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000000",
        json={"name": "X"},
    )
    assert resp.status_code == 404


def test_archive_and_unarchive(client: TestClient) -> None:
    created = client.post("/api/v1/workspaces", json={"name": "WS"}).json()

    resp = client.post(f"/api/v1/workspaces/{created['id']}/archive")
    assert resp.status_code == 200
    assert resp.json()["is_archived"] is True

    resp = client.post(f"/api/v1/workspaces/{created['id']}/unarchive")
    assert resp.status_code == 200
    assert resp.json()["is_archived"] is False


def test_soft_delete_hides_from_list(client: TestClient) -> None:
    created = client.post("/api/v1/workspaces", json={"name": "WS"}).json()

    resp = client.delete(f"/api/v1/workspaces/{created['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"id": created["id"], "deleted": True}

    # No longer in default list.
    body = client.get("/api/v1/workspaces").json()
    assert created["id"] not in {item["id"] for item in body["items"]}

    # Direct GET also 404s because the row is soft-deleted.
    resp = client.get(f"/api/v1/workspaces/{created['id']}")
    assert resp.status_code == 404


def test_delete_workspace_not_found(client: TestClient) -> None:
    resp = client.delete("/api/v1/workspaces/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404
