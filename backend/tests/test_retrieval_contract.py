"""Retrieval exclusion contract tests (RG-2 / D1).

These verify that:
  1. ``milvus_client.search`` pushes ``exclude_paper_ids`` into the filter
     expression (not a post-filter), so excluded papers never enter recall.
  2. The three retrieval service functions forward exclusion sets and
     surface them on ``filters_applied`` for audit.
  3. ``find_similar_work`` ALWAYS excludes its own source paper.
  4. Workspace isolation is part of the Milvus filter expression.

All Milvus/embedding interactions are mocked — these test the contract
wiring, not vector search itself.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.domains.artifact.service import ArtifactService
from app.domains.paper.models import Paper
from app.domains.retrieval import milvus_client, service
from app.domains.retrieval.schemas import RetrievalResponse
from app.domains.workspace.models import Workspace


# ==================================================================
# milvus_client.search filter push-down
# ==================================================================


class _RecordingMilvus:
    """Stand-in for pymilvus MilvusClient that records filter expressions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def has_collection(self, name: str) -> bool:
        return True

    def load_collection(self, name: str) -> None:
        pass

    def search(self, *, collection_name, data, limit, filter, output_fields, search_params):
        self.calls.append({"filter": filter, "limit": limit, "data_len": len(data[0])})
        return [[]]


def _patch_milvus_client(monkeypatch) -> _RecordingMilvus:
    fake = _RecordingMilvus()
    monkeypatch.setattr(milvus_client, "get_milvus_client", lambda: fake)
    return fake


def test_search_excludes_papers_in_filter(monkeypatch) -> None:
    fake = _patch_milvus_client(monkeypatch)
    milvus_client.search([0.1] * 4, "ws-1", top_k=10, exclude_paper_ids={"p-2", "p-1"})
    expr = fake.calls[0]["filter"]
    assert 'workspace_id == "ws-1"' in expr
    # Sorted → deterministic filter string.
    assert 'paper_id not in ["p-1", "p-2"]' in expr


def test_search_combines_paper_id_and_exclude(monkeypatch) -> None:
    fake = _patch_milvus_client(monkeypatch)
    milvus_client.search(
        [0.1] * 4,
        "ws-1",
        top_k=10,
        paper_id="target",
        exclude_paper_ids={"p-9"},
        section="Method",
    )
    expr = fake.calls[0]["filter"]
    assert 'paper_id == "target"' in expr
    assert 'paper_id not in ["p-9"]' in expr
    assert 'section == "Method"' in expr


def test_search_workspace_isolation_in_filter(monkeypatch) -> None:
    fake = _patch_milvus_client(monkeypatch)
    milvus_client.search([0.1] * 4, "ws-42", top_k=5)
    expr = fake.calls[0]["filter"]
    assert 'workspace_id == "ws-42"' in expr
    assert expr.count("workspace_id") == 1  # not duplicated


def test_search_empty_exclude_omits_clause(monkeypatch) -> None:
    fake = _patch_milvus_client(monkeypatch)
    milvus_client.search([0.1] * 4, "ws-1", top_k=10, exclude_paper_ids=set())
    expr = fake.calls[0]["filter"]
    assert "paper_id not in" not in expr


# ==================================================================
# service-level exclusion forwarding
# ==================================================================


class _FakeEmbedding:
    def embed_one(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    def embed_texts(self, texts: list[str]):
        from types import SimpleNamespace

        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3, 0.4]] * len(texts))


class _FakeMilvus:
    """Replaces ``service.milvus_client``; records the search arguments."""

    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self.hits = hits
        self.calls: list[dict[str, Any]] = []

    def search(self, query_vector, workspace_id, top_k=10, *, paper_id=None, exclude_paper_ids=None, section=None):
        self.calls.append(
            {
                "query_vector": query_vector,
                "workspace_id": workspace_id,
                "top_k": top_k,
                "paper_id": paper_id,
                "exclude_paper_ids": exclude_paper_ids,
                "section": section,
            }
        )
        return self.hits


def _patch_service_deps(monkeypatch, hits: list[dict[str, Any]]) -> _FakeMilvus:
    fake = _FakeMilvus(hits)
    monkeypatch.setattr(service, "milvus_client", fake)
    monkeypatch.setattr(service, "get_embedding_gateway", _FakeEmbedding)
    return fake


def test_counter_evidence_forwards_exclude_to_milvus(monkeypatch) -> None:
    fake = _patch_service_deps(
        monkeypatch,
        hits=[{"chunk_id": "c1", "workspace_id": "ws-1", "paper_id": "p-other",
               "section": "M", "text": "t", "score": 0.9,
               "source_artifact_id": "a1", "chunk_index": 1}],
    )
    resp = service.find_counter_evidence(
        "ws-1", "some claim", top_k=5,
        use_reranker=False, use_judge=False,
        exclude_paper_ids={"p-source"},
    )
    assert fake.calls[0]["exclude_paper_ids"] == {"p-source"}
    assert resp.filters_applied["excluded_paper_ids"] == ["p-source"]
    assert resp.items[0].paper_id == "p-other"  # only the NON-excluded paper survives


def test_counter_evidence_never_returns_excluded_paper(monkeypatch) -> None:
    """Even if a buggy Milvus returns excluded chunks, the service still
    (defensively) drops them before returning — belt-and-suspenders on top
    of the filter push-down."""
    hits = [
        {"chunk_id": "c1", "workspace_id": "ws-1", "paper_id": "p-source",
         "section": "M", "text": "t", "score": 0.9, "source_artifact_id": "a1", "chunk_index": 1},
        {"chunk_id": "c2", "workspace_id": "ws-1", "paper_id": "p-source",
         "section": "M", "text": "t2", "score": 0.8, "source_artifact_id": "a1", "chunk_index": 2},
        {"chunk_id": "c3", "workspace_id": "ws-1", "paper_id": "p-other",
         "section": "M", "text": "t3", "score": 0.7, "source_artifact_id": "a1", "chunk_index": 3},
    ]
    _patch_service_deps(monkeypatch, hits=hits)
    resp = service.find_counter_evidence(
        "ws-1", "some claim", top_k=5,
        use_reranker=False, use_judge=False,
        exclude_paper_ids={"p-source"},
    )
    assert all(item.paper_id != "p-source" for item in resp.items)
    assert len(resp.items) == 1
    assert resp.items[0].paper_id == "p-other"


def test_similar_work_always_excludes_source_paper(
    db_session, monkeypatch, tmp_path
) -> None:
    """find_similar_work must put the source paper in the exclusion set
    even when the caller passes no exclusions."""
    fake = _patch_service_deps(
        monkeypatch,
        hits=[{"chunk_id": "c1", "workspace_id": "ws-1", "paper_id": "p-src",
               "section": "M", "text": "t", "score": 0.9, "source_artifact_id": "a1", "chunk_index": 1}],
    )
    monkeypatch.setattr(
        "app.core.config.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    workspace_id = str(uuid4())
    paper_id = str(uuid4())
    db_session.add(
        Workspace(id=workspace_id, name="Retrieval Contract", is_deleted=False)
    )
    paper = Paper(
        id=paper_id,
        workspace_id=workspace_id,
        title="Source paper",
        authors=[],
        source="manual",
        is_deleted=False,
    )
    db_session.add(paper)
    db_session.flush()
    payload = "\n".join(
        json.dumps({
            "chunk_id": f"src-{i}",
            "workspace_id": workspace_id,
            "paper_id": paper_id,
            "source_artifact_id": "art-1",
            "chunk_index": i,
            "text": "source chunk",
            "start_char": 0,
            "end_char": 10,
        })
        for i in range(3)
    )
    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace_id,
        filename=f"{paper_id}_chunks.jsonl",
        content=payload.encode("utf-8"),
        mime_type="application/jsonl",
        kind="chunk_index",
    )
    paper.chunk_index_artifact_id = artifact.id
    db_session.commit()

    resp = service.find_similar_work(
        workspace_id,
        paper_id,
        top_k=5,
        db=db_session,
        use_reranker=False,
    )
    assert paper_id in (fake.calls[0]["exclude_paper_ids"] or set())
    assert resp.filters_applied["excluded_paper_ids"] == [paper_id]


def test_semantic_search_forwards_exclude(monkeypatch) -> None:
    fake = _patch_service_deps(monkeypatch, hits=[])
    resp = service.semantic_search("ws-1", "query", top_k=5, exclude_paper_ids={"p-x"})
    assert fake.calls[0]["exclude_paper_ids"] == {"p-x"}
    assert resp.filters_applied["excluded_paper_ids"] == ["p-x"]
    # Empty result is "succeeded" (no hits), NOT "failed" — absence of a
    # counter-example must not be reported as a system failure.
    assert resp.status == "succeeded"
    assert resp.items == []
