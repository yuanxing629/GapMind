"""Paper 上传、Artifact 和 Timeline 链路的集成测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _create_workspace(client: TestClient, name: str = "WS") -> dict:
    resp = client.post("/api/v1/workspaces", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _pdf_bytes(content: str = "%PDF-1.4 fake pdf body") -> bytes:
    return content.encode("utf-8")


# ----------------------------------------------------------------- 上传
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

# Artifact 会出现在列表中。
    arts = client.get(f"/api/v1/workspaces/{wid}/artifacts").json()
    assert len(arts) == 1
    assert arts[0]["id"] == paper["primary_artifact_id"]
    assert arts[0]["kind"] == "pdf"
    assert arts[0]["size_bytes"] > 0
    assert arts[0]["original_filename"] == "paper1.pdf"

# Timeline 记录了上传事件。
    timeline = client.get(f"/api/v1/workspaces/{wid}/timeline").json()
    types = [e["event_type"] for e in timeline["items"]]
    assert "paper.uploaded" in types
# Workspace.created 是否也由 workspace 创建流程记录？不是——Phase 1b workspace service
# 尚未写入 timeline，当前只有 paper/task 会写入。
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


def test_upload_paper_rejects_non_pdf_content_even_with_pdf_extension(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("not-really-a-pdf.pdf", b"<html>not a PDF</html>", "application/pdf")},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_pdf"


def test_upload_paper_respects_workspace_storage_quota(client: TestClient, monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "workspace_storage_quota_bytes", 8)
    ws = _create_workspace(client)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("quota.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 413
    assert resp.json()["detail"]["error"] == "workspace_storage_quota_exceeded"


def test_upload_paper_falls_back_to_filename_as_title(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("GNNExplainer-paper.pdf", _pdf_bytes(), "application/pdf")},
    )
    assert resp.status_code == 201
    assert resp.json()["title"] == "GNNExplainer-paper"


# ------------------------------------------------------ 仅元数据创建
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

# 不应创建 artifact。
    arts = client.get(f"/api/v1/workspaces/{ws['id']}/artifacts").json()
    assert arts == []


# --------------------------------------------------------------- 列表 / 获取
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
# Paper 属于 A，通过 B 查询应返回 404。
    resp = client.get(f"/api/v1/workspaces/{ws_b['id']}/papers/{paper['id']}")
    assert resp.status_code == 404


# ------------------------------------------------------------------ 更新
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


# ------------------------------------------------------------------ 删除
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


def test_external_search_history_and_favorites_are_owner_scoped(
    client: TestClient, monkeypatch
) -> None:
    alice = {"X-User-ID": "alice"}
    bob = {"X-User-ID": "bob"}

    def fake_search(self, **kwargs):
        del self, kwargs
        return {
            "total": 1,
            "offset": 0,
            "data": [{"paperId": "s2-1", "title": "A shared paper"}],
        }

    monkeypatch.setattr("app.domains.paper.router.SemanticScholarClient.search", fake_search)

    response = client.get("/api/v1/papers/search?query=graph", headers=alice)
    assert response.status_code == 200, response.text
    assert len(client.get("/api/v1/papers/search/history", headers=alice).json()) == 1
    assert client.get("/api/v1/papers/search/history", headers=bob).json() == []

    favorite_payload = {"paper": {"paperId": "s2-1", "title": "A shared paper"}}
    assert client.post("/api/v1/papers/favorites", headers=alice, json=favorite_payload).status_code == 200
    assert client.get("/api/v1/papers/favorites", headers=bob).json() == []

# 同一外部论文可以由另一用户独立收藏。
    assert client.post("/api/v1/papers/favorites", headers=bob, json=favorite_payload).status_code == 200
    assert len(client.get("/api/v1/papers/favorites", headers=alice).json()) == 1
    assert len(client.get("/api/v1/papers/favorites", headers=bob).json()) == 1

    history_id = client.get("/api/v1/papers/search/history", headers=alice).json()[0]["id"]
    assert client.delete(
        f"/api/v1/papers/search/history/{history_id}", headers=bob
    ).status_code == 404


# --------------------------------------------------------------- timeline：时间线
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

# 按 subject_id 过滤有效。
    paper_events = client.get(
        f"/api/v1/workspaces/{ws['id']}/timeline",
        params={"subject_type": "paper", "subject_id": paper["id"]},
    ).json()
    assert all(e["subject_type"] == "paper" for e in paper_events["items"])
    assert paper_events["total"] == 3
