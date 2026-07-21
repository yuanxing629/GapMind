"""Integration tests for the Paper upload + Artifact + Timeline chain."""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_workspace(client: TestClient, name: str = "WS") -> dict:
    resp = client.post("/api/v1/workspaces", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _pdf_bytes(content: str = "%PDF-1.4 fake pdf body") -> bytes:
    return content.encode("utf-8")


# ----------------------------------------------------------------- upload
def test_upload_paper_creates_artifact_and_timeline(client: TestClient) -> None:
    ws = _create_workspace(client, "UploadWS")
    wid = ws["id"]

    resp = client.post(
        f"/api/v1/workspaces/{wid}/papers/upload",
        files={"file": ("paper1.pdf", _pdf_bytes(), "application/pdf")},
        data={"title": "Self-Interpretable GNNs", "authors": "Alice, Bob", "year": "2024"},
    )
    assert resp.status_code == 201, resp.text
    paper = resp.json()
    assert paper["title"] == "Self-Interpretable GNNs"
    assert paper["authors"] == ["Alice", "Bob"]
    assert paper["year"] == 2024
    assert paper["primary_artifact_id"] is not None

    # Artifact is listed.
    arts = client.get(f"/api/v1/workspaces/{wid}/artifacts").json()
    assert len(arts) == 1
    assert arts[0]["id"] == paper["primary_artifact_id"]
    assert arts[0]["kind"] == "pdf"
    assert arts[0]["size_bytes"] > 0
    assert arts[0]["original_filename"] == "paper1.pdf"

    # Timeline captured the upload event.
    timeline = client.get(f"/api/v1/workspaces/{wid}/timeline").json()
    types = [e["event_type"] for e in timeline["items"]]
    assert "paper.uploaded" in types
    # Workspace.created is also recorded by the workspace creation flow? No -
    # Phase 1b workspace service does not yet emit timeline. Only paper/task do.
    assert "workspace.created" not in types


def test_upload_paper_rejects_non_pdf(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_file"


def test_upload_paper_rejects_empty_file(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "empty_file"


def test_upload_paper_falls_back_to_filename_as_title(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("GNNExplainer-paper.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == "GNNExplainer-paper"


# ------------------------------------------------------ metadata-only create
def test_create_paper_metadata_only(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers",
        json={"title": "A Survey", "authors": ["X"], "year": 2023},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["primary_artifact_id"] is None
    assert body["source"] == "manual"

    # No artifact created.
    arts = client.get(f"/api/v1/workspaces/{ws['id']}/artifacts").json()
    assert arts == []


# --------------------------------------------------------------- list / get
def test_list_papers(client: TestClient) -> None:
    ws = _create_workspace(client)
    for i in range(3):
        client.post(
            f"/api/v1/workspaces/{ws['id']}/papers",
            json={"title": f"P{i}", "authors": ["A"]},
        )
    resp = client.get(f"/api/v1/workspaces/{ws['id']}/papers")
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_get_paper_cross_workspace_404(client: TestClient) -> None:
    ws_a = _create_workspace(client, "A")
    ws_b = _create_workspace(client, "B")
    paper = client.post(
        f"/api/v1/workspaces/{ws_a['id']}/papers",
        json={"title": "P"},
    ).json()
    # Paper belongs to A; asking via B should 404.
    resp = client.get(f"/api/v1/workspaces/{ws_b['id']}/papers/{paper['id']}")
    assert resp.status_code == 404


# ------------------------------------------------------------------ update
def test_update_paper(client: TestClient) -> None:
    ws = _create_workspace(client)
    paper = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers",
        json={"title": "Old Title"},
    ).json()
    resp = client.patch(
        f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}",
        json={"title": "New Title", "year": 2024},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "New Title"
    assert body["year"] == 2024


# ------------------------------------------------------------------ delete
def test_soft_delete_paper_hides_from_list(client: TestClient) -> None:
    ws = _create_workspace(client)
    paper = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers",
        json={"title": "P"},
    ).json()
    resp = client.delete(f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}")
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True

    body = client.get(f"/api/v1/workspaces/{ws['id']}/papers").json()
    assert paper["id"] not in {p["id"] for p in body["items"]}


# --------------------------------------------------------------- timeline
def test_timeline_records_paper_events(client: TestClient) -> None:
    ws = _create_workspace(client)
    paper = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers",
        json={"title": "P"},
    ).json()
    client.patch(
        f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}",
        json={"title": "P2"},
    )
    client.delete(f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}")

    timeline = client.get(f"/api/v1/workspaces/{ws['id']}/timeline").json()
    types = [e["event_type"] for e in timeline["items"]]
    assert "paper.created" in types
    assert "paper.updated" in types
    assert "paper.deleted" in types

    # Filter by subject_id works.
    paper_events = client.get(
        f"/api/v1/workspaces/{ws['id']}/timeline",
        params={"subject_type": "paper", "subject_id": paper["id"]},
    ).json()
    assert all(e["subject_type"] == "paper" for e in paper_events["items"])
    assert paper_events["total"] == 3
