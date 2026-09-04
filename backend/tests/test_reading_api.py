"""阅读库与页面级标注集成测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _paper(client: TestClient) -> dict:
    workspace = client.post("/api/v1/workspaces", json={"name": "Reading WS"}).json()
    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "A Paper to Read", "authors": ["Alice"], "year": 2025},
    ).json()
    return {"workspace": workspace, "paper": paper}


def test_reading_library_progress_and_annotations(client: TestClient) -> None:
    data = _paper(client)
    paper_id = data["paper"]["id"]

    added = client.post(f"/api/v1/reading/papers/{paper_id}")
    assert added.status_code == 201, added.text
    assert added.json()["reading_status"] == "unread"

    listed = client.get("/api/v1/reading/papers")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["paper_id"] == paper_id

    progress = client.patch(
        f"/api/v1/reading/papers/{paper_id}/progress",
        json={"page_number": 7, "status": "reading"},
    )
    assert progress.status_code == 200
    assert progress.json()["last_read_page"] == 7
    assert progress.json()["reading_status"] == "reading"

    annotation = client.post(
        f"/api/v1/reading/papers/{paper_id}/annotations",
        json={
            "page_number": 7,
            "selected_text": "A useful claim.",
            "note_content": "Need to verify this claim against the baseline.",
        },
    )
    assert annotation.status_code == 201, annotation.text
    annotation_id = annotation.json()["id"]
    assert annotation.json()["artifact_id"] is None

    annotations = client.get(f"/api/v1/reading/papers/{paper_id}/annotations")
    assert annotations.status_code == 200
    assert annotations.json()[0]["page_number"] == 7

    deleted_annotation = client.delete(f"/api/v1/reading/annotations/{annotation_id}")
    assert deleted_annotation.status_code == 200
    assert client.get(f"/api/v1/reading/papers/{paper_id}/annotations").json() == []


def test_reading_item_can_be_removed_and_readded(client: TestClient) -> None:
    data = _paper(client)
    paper_id = data["paper"]["id"]

    client.post(f"/api/v1/reading/papers/{paper_id}")
    removed = client.delete(f"/api/v1/reading/papers/{paper_id}")
    assert removed.status_code == 200
    assert client.get("/api/v1/reading/papers").json()["total"] == 0

    readded = client.post(f"/api/v1/reading/papers/{paper_id}")
    assert readded.status_code == 201
    assert readded.json()["paper_id"] == paper_id


def test_reading_paper_and_annotation_routes_are_owner_scoped(client: TestClient) -> None:
    headers = {"X-User-ID": "alice"}
    workspace = client.post(
        "/api/v1/workspaces", headers=headers, json={"name": "Alice Reading WS"}
    ).json()
    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        headers=headers,
        json={"title": "Alice Paper", "authors": ["Alice"], "year": 2025},
    ).json()

    other_headers = {"X-User-ID": "bob"}
    assert client.post(
        f"/api/v1/reading/papers/{paper['id']}", headers=other_headers
    ).status_code == 404
    assert client.get(
        "/api/v1/reading/papers", headers=other_headers
    ).json()["total"] == 0
