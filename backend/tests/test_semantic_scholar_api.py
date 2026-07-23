"""Tests for the Semantic Scholar search proxy and metadata import flow."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.domains.paper.router import _get_semantic_scholar_client
from app.main import app


class FakeSemanticScholarClient:
    def __init__(self) -> None:
        self.search_kwargs: dict[str, object] | None = None

    def search(self, **kwargs):  # type: ignore[no-untyped-def]
        self.search_kwargs = kwargs
        return {
            "total": 1,
            "offset": 0,
            "next": None,
            "data": [
                {
                    "paperId": "s2-paper-1",
                    "title": "A GNN Paper",
                    "authors": [{"authorId": "a1", "name": "Alice"}],
                    "year": 2024,
                    "citationCount": 12,
                    "referenceCount": 8,
                }
            ],
        }

    def get_paper(self, paper_id: str, *, fields: str):  # type: ignore[no-untyped-def]
        assert paper_id == "s2-paper-1"
        assert "externalIds" in fields
        return {
            "paperId": paper_id,
            "externalIds": {"DOI": "10.1234/example", "ArXiv": "2401.12345"},
            "title": "A GNN Paper",
            "abstract": "An abstract.",
            "year": 2024,
            "authors": [{"authorId": "a1", "name": "Alice"}],
        }


def _create_workspace(client: TestClient) -> dict:
    response = client.post("/api/v1/workspaces", json={"name": "S2 Workspace"})
    assert response.status_code == 201, response.text
    return response.json()


def test_semantic_search_forwards_filters_and_sort(client: TestClient) -> None:
    fake = FakeSemanticScholarClient()
    app.dependency_overrides[_get_semantic_scholar_client] = lambda: fake

    response = client.get(
        "/api/v1/papers/search",
        params={
            "query": "graph neural networks",
            "year_from": 2020,
            "year_to": 2024,
            "min_citation_count": 10,
            "open_access": "true",
            "sort": "citationCount:desc",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"][0]["paperId"] == "s2-paper-1"
    assert fake.search_kwargs is not None
    assert fake.search_kwargs["sort"] == "citationCount:desc"
    assert fake.search_kwargs["year"] == "2020-2024"
    assert fake.search_kwargs["minCitationCount"] == 10
    assert fake.search_kwargs["openAccessPdf"] == ""


def test_import_semantic_scholar_paper_creates_metadata_paper(client: TestClient) -> None:
    fake = FakeSemanticScholarClient()
    app.dependency_overrides[_get_semantic_scholar_client] = lambda: fake
    workspace = _create_workspace(client)

    response = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers/import-from-s2",
        json={"semantic_scholar_paper_id": "s2-paper-1"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "semantic_scholar"
    assert body["external_paper_id"] == "s2-paper-1"
    assert body["doi"] == "10.1234/example"
    assert body["arxiv_id"] == "2401.12345"
    assert body["primary_artifact_id"] is None


def test_import_semantic_scholar_paper_is_idempotent(client: TestClient) -> None:
    fake = FakeSemanticScholarClient()
    app.dependency_overrides[_get_semantic_scholar_client] = lambda: fake
    workspace = _create_workspace(client)
    url = f"/api/v1/workspaces/{workspace['id']}/papers/import-from-s2"
    payload = {"semantic_scholar_paper_id": "s2-paper-1"}

    first = client.post(url, json=payload)
    second = client.post(url, json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
