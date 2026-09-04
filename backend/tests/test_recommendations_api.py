"""工作区范围论文推荐测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.domains.recommendation.router import _client
from app.main import app


class FakeRecommendationClient:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        self.calls += 1
        return {
            "total": 3,
            "offset": 0,
            "next": None,
            "data": [
                {
                    "paperId": "candidate-1",
                    "title": "Graph Neural Networks for Traffic Forecasting",
                    "abstract": "A recent method for spatiotemporal traffic prediction.",
                    "year": 2024,
                    "citationCount": 30,
                    "isOpenAccess": True,
                    "openAccessPdf": {"url": "https://example.com/candidate-1.pdf"},
                    "authors": [{"name": "Alice"}],
                    "url": "https://www.semanticscholar.org/paper/candidate-1",
                },
                {
                    "paperId": "candidate-2",
                    "title": "A Survey of Graph Representation Learning",
                    "abstract": "A survey of graph neural networks.",
                    "year": 2021,
                    "citationCount": 100,
                    "authors": [{"name": "Bob"}],
                },
                {
                    "paperId": "candidate-1",
                    "title": "Graph Neural Networks for Traffic Forecasting",
                    "abstract": "Duplicate returned by another query.",
                    "year": 2024,
                    "citationCount": 30,
                    "authors": [{"name": "Alice"}],
                },
            ],
        }


def _create_workspace(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={
            "name": "Traffic Research",
            "topic": "Graph neural networks for traffic forecasting",
            "keywords": ["spatiotemporal modeling", "traffic prediction"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_recommendations_generate_cache_and_filter_existing_papers(client: TestClient) -> None:
    fake = FakeRecommendationClient()
    app.dependency_overrides[_client] = lambda: fake
    workspace = _create_workspace(client)
    wid = workspace["id"]

    existing = client.post(
        f"/api/v1/workspaces/{wid}/papers",
        json={"title": "A Survey of Graph Representation Learning"},
    )
    assert existing.status_code == 201

    first = client.get(f"/api/v1/workspaces/{wid}/recommendations")
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["has_profile"] is True
    assert body["profile_topics"]
    assert {item["external_paper_id"] for item in body["items"]} == {"candidate-1"}
    assert body["items"][0]["reasons"]
    assert fake.calls == 3

    cached = client.get(f"/api/v1/workspaces/{wid}/recommendations")
    assert cached.status_code == 200
    assert fake.calls == 3


def test_recommendation_feedback_dismisses_item(client: TestClient) -> None:
    fake = FakeRecommendationClient()
    app.dependency_overrides[_client] = lambda: fake
    workspace = _create_workspace(client)
    wid = workspace["id"]

    generated = client.get(f"/api/v1/workspaces/{wid}/recommendations").json()
    external_id = generated["items"][0]["external_paper_id"]
    feedback = client.post(
        f"/api/v1/workspaces/{wid}/recommendations/{external_id}/feedback",
        json={"action": "dismiss"},
    )
    assert feedback.status_code == 200
    assert feedback.json()["status"] == "dismissed"

    current = client.get(f"/api/v1/workspaces/{wid}/recommendations")
    assert current.status_code == 200
    assert external_id not in {item["external_paper_id"] for item in current.json()["items"]}
    assert fake.calls == 3
