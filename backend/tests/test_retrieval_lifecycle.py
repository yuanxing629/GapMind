"""Indexing lifecycle + degradation-path tests (RG-6 / V3).

Contract items from phase3_smoke_validation_and_next_plan.md §6 V3:

  1. Same paper re-embed does NOT produce duplicate chunks.
  2. Chunk version change → old vectors are fully deleted.
  3. Paper soft-deleted → Retrieval no longer returns that paper.
  4. Workspace archive / soft-delete policy is consistent with retrieval.
  5. Embedding / Milvus / reranker failures → explicit failed/degraded status,
     NOT silent fallback to "empty success".
  6. Task and Paper projected state don't lie about real Milvus state.
  7. Fresh-DB end-to-end (migration → upload → parse → extract → index → search).

Most tests mock Milvus (no live Milvus instance in CI) and use the
SQLite fixture for Paper / Task / Workspace state. The cross-domain
deletion propagation in (3) is tested by mocking milvus_client.delete_by_paper
and asserting it gets called on paper soft_delete.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.artifact.service import ArtifactService
from app.domains.paper.models import Paper
from app.domains.paper.service import PaperService
from app.domains.retrieval import milvus_client, service as retrieval_service
from app.domains.workspace.models import Workspace


# ==================================================================
# Fixtures
# ==================================================================


@pytest.fixture
def fake_milvus(_stub_milvus) -> MagicMock:
    """Re-export the conftest-provided Milvus fake so test bodies can refer to
    it explicitly. Returning the same object (not a fresh MagicMock) means
    assertions like ``fake_milvus.delete_by_paper.assert_called_once_with(...)``
    see the same calls that paper.service / retrieval.service triggered via
    their patched module attributes.
    """
    return _stub_milvus


@pytest.fixture
def fake_embedding(monkeypatch) -> MagicMock:
    fake = MagicMock(name="embedding")
    fake.model = "fake-emb"
    fake.dim = 4
    fake.embed_one.return_value = [0.1, 0.2, 0.3, 0.4]
    # Return one embedding per input text (pymilvus contract).
    def _fake_embed(texts):
        from types import SimpleNamespace
        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3, 0.4] for _ in texts])

    fake.embed_texts.side_effect = _fake_embed
    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: fake)
    return fake


def _workspace(db: Session, *, archived: bool = False) -> Workspace:
    import uuid as _uuid
    ws = Workspace(id=str(_uuid.uuid4()), name="Lifecycle Test", is_deleted=False, is_archived=archived)
    db.add(ws)
    db.commit()
    return ws


def _paper(
    db: Session,
    ws_id: str,
    *,
    chunk_count: int = 0,
    parse_status: str = "parsed",
) -> Paper:
    import uuid as _uuid
    paper = Paper(
        id=str(_uuid.uuid4()),
        workspace_id=ws_id,
        title="Lifecycle Test Paper",
        authors=[],
        source="manual",
        parse_status=parse_status,
        chunk_count=chunk_count,
        is_deleted=False,
    )
    db.add(paper)
    db.commit()
    return paper


def _write_chunks_jsonl(db: Session, paper: Paper, n: int):
    """Create the canonical chunk_index Artifact for a synthetic paper."""
    payload = "\n".join(
        json.dumps({
            "chunk_id": f"{paper.id}-c{i}",
            "workspace_id": paper.workspace_id,
            "paper_id": paper.id,
            "source_artifact_id": "art-1",
            "chunk_index": i,
            "text": f"chunk {i}",
            "start_char": 0,
            "end_char": 10,
        })
        for i in range(n)
    )
    artifact = ArtifactService(db).save_upload(
        workspace_id=paper.workspace_id,
        filename=f"{paper.id}_chunks.jsonl",
        content=payload.encode("utf-8"),
        mime_type="application/jsonl",
        kind="chunk_index",
    )
    paper.chunk_index_artifact_id = artifact.id
    db.commit()
    return artifact


def test_chunk_records_resolve_from_storage_artifact(
    db_session, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=2)

    chunk = retrieval_service.find_chunk_record(
        ws.id,
        paper.id,
        f"{paper.id}-c1",
        db=db_session,
    )
    assert chunk is not None
    assert chunk.start_char == 0
    assert chunk.end_char == 10

    # Historical rows without the pointer can still resolve the exact
    # workspace-scoped chunk_index filename through the DB metadata.
    paper.chunk_index_artifact_id = None
    db_session.commit()
    assert retrieval_service.find_chunk_record(
        ws.id,
        paper.id,
        f"{paper.id}-c1",
        db=db_session,
    ) is not None
    assert retrieval_service.find_chunk_record(
        str(uuid4()),
        paper.id,
        f"{paper.id}-c1",
        db=db_session,
    ) is None


# ==================================================================
# 1. Idempotent indexing
# ==================================================================


def test_index_paper_chunks_idempotent_skips_existing(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """Same paper indexed twice without force_reindex — second call
    invokes get_existing_chunk_ids and inserts zero new chunks."""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    # Conftest's stub returns insert_chunks=0 by default; simulate a real
    # Milvus by returning the batch size.
    fake_milvus.insert_chunks.side_effect = lambda batch: len(batch)
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=3)

    # First index: nothing exists yet → insert all 3
    fake_milvus.get_existing_chunk_ids.return_value = set()
    r1 = retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert r1.indexed_count == 3
    assert r1.skipped_count == 0

    # Second index: report all 3 already exist → skip
    fake_milvus.get_existing_chunk_ids.return_value = {
        f"{paper.id}-c0", f"{paper.id}-c1", f"{paper.id}-c2",
    }
    r2 = retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert r2.indexed_count == 0
    assert r2.skipped_count == 3
    assert r2.error is None


# ==================================================================
# 2. Force reindex deletes old vectors before re-inserting
# ==================================================================


def test_index_force_reindex_calls_delete_by_paper_first(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """force_reindex=True must call delete_by_paper so stale chunks from a
    previous parse don't linger. If a chunk_id survives across parses, it
    gets re-inserted (chunk_version change implies new content)."""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=3)

    retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session, force_reindex=True)

    fake_milvus.delete_by_paper.assert_called_once_with(paper.id)
    # delete happens before insert; verify call ordering.
    call_order = [c[0] for c in fake_milvus.mock_calls]
    assert "delete_by_paper" in call_order
    assert "insert_chunks" in call_order
    assert call_order.index("delete_by_paper") < call_order.index("insert_chunks")


# ==================================================================
# 3. Soft-deleted paper → no Retrieval hits
# ==================================================================


def test_paper_soft_delete_propagates_to_milvus(
    db_session, monkeypatch
) -> None:
    """When paper.is_deleted flips to True, the corresponding Milvus
    vectors must be deleted so future Retrieval calls don't surface it.

    This is a cross-domain propagation contract — paper lifecycle events
    must reach the search index. We assert it by patching the
    ``milvus_client`` module attribute that paper.service holds.
    """
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)

    fake_milvus = MagicMock(name="milvus_client")
    import app.domains.paper.service as paper_service_module
    monkeypatch.setattr(paper_service_module.milvus_client, "delete_by_paper", fake_milvus.delete_by_paper)

    PaperService(db_session).soft_delete(paper.id)

    fake_milvus.delete_by_paper.assert_called_once_with(paper.id)


def test_soft_deleted_paper_excluded_from_retrieval_via_milvus_deletion(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """End-to-end: index a paper, soft-delete it (which deletes from
    Milvus), then verify retrieval returns nothing from that paper.

    The conftest's autouse _stub_milvus fixture provides a shared MagicMock
    Milvus; the explicit fake_milvus parameter here is the SAME object (it's
    a function-scoped autouse fixture, and `fake_milvus` re-imports it for
    the test's convenience). So we just assert on the shared fake.
    """
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=2)

    # Index.
    fake_milvus.get_existing_chunk_ids.return_value = set()
    retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert fake_milvus.insert_chunks.call_count >= 1

    # Soft-delete → should propagate to Milvus.
    PaperService(db_session).soft_delete(paper.id)
    assert fake_milvus.delete_by_paper.call_count == 1

    # Now retrieval returns no hits because Milvus is empty for that paper.
    fake_milvus.search.return_value = []
    resp = retrieval_service.semantic_search(ws.id, "anything")
    assert resp.total == 0
    assert resp.status == "succeeded"  # empty retrieval is NOT a failure


# ==================================================================
# 4. Workspace archive / soft-delete contract
# ==================================================================


def test_workspace_is_archived_does_not_affect_retrieval(
    db_session, fake_milvus, fake_embedding
) -> None:
    """Archive is a non-destructive flag (workspace preserved for history).
    Retrieval against an archived workspace still works — the user can
    query history. (Soft-delete is the destructive variant.)"""
    ws = _workspace(db_session, archived=True)

    fake_milvus.search.return_value = [
        {"chunk_id": "c1", "workspace_id": ws.id, "paper_id": "p-A",
         "section": "M", "text": "t", "score": 0.9,
         "source_artifact_id": "a1", "chunk_index": 1},
    ]

    resp = retrieval_service.semantic_search(ws.id, "query")
    assert resp.status == "succeeded"
    assert resp.total == 1
    # The filter still pins the query to the workspace's scope.
    fake_milvus.search.assert_called_once()
    # workspace_id is the first positional arg of milvus_client.search.
    args, kwargs = fake_milvus.search.call_args
    assert args[1] == ws.id  # (query_vector, workspace_id, top_k=...)


# ==================================================================
# 5. Failure paths: explicit status, no silent fallback
# ==================================================================


class _FakeEmbeddingBoom:
    """embed_one raises — retrieval must surface a clean failed status,
    NOT pretend the search succeeded with empty results."""

    model = "fake"
    dim = 4

    def embed_one(self, text: str):
        raise RuntimeError("upstream embedding provider 503")

    def embed_texts(self, texts):
        raise RuntimeError("upstream embedding provider 503")


class _FakeMilvusBoom:
    def search(self, *args, **kwargs):
        raise RuntimeError("milvus connection refused")


def test_semantic_search_status_failed_on_embedding_error(monkeypatch) -> None:
    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: _FakeEmbeddingBoom())
    monkeypatch.setattr(retrieval_service, "milvus_client", _FakeMilvusBoom())

    resp = retrieval_service.semantic_search("ws-1", "query", top_k=5)
    assert resp.status == "failed"
    assert resp.items == []
    assert resp.error is not None and "embedding" in resp.error.lower()
    assert resp.diagnostic_code == "embedding_unavailable"


def test_semantic_search_status_failed_on_milvus_error(monkeypatch) -> None:
    class _OkEmbedding:
        model = "fake"
        dim = 4
        def embed_one(self, text): return [0.1] * 4
        def embed_texts(self, texts):
            from types import SimpleNamespace
            return SimpleNamespace(embeddings=[[0.1] * 4] * len(texts))

    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: _OkEmbedding())
    monkeypatch.setattr(retrieval_service, "milvus_client", _FakeMilvusBoom())

    resp = retrieval_service.semantic_search("ws-1", "query", top_k=5)
    assert resp.status == "failed"
    assert "milvus" in (resp.error or "").lower()
    assert resp.diagnostic_code == "milvus_unavailable"


def test_semantic_search_status_failed_on_unloaded_collection(monkeypatch) -> None:
    class _OkEmbedding:
        def embed_one(self, text): return [0.1] * 4

    class _UnloadedCollection:
        def search(self, *args, **kwargs):
            raise RuntimeError("collection not loaded")

    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: _OkEmbedding())
    monkeypatch.setattr(retrieval_service, "milvus_client", _UnloadedCollection())

    resp = retrieval_service.semantic_search("ws-1", "query", top_k=5)
    assert resp.status == "failed"
    assert resp.diagnostic_code == "collection_unloaded"
    assert "重建索引" in (resp.error or "")


def test_counter_evidence_status_failed_on_milvus_error(monkeypatch) -> None:
    """Counter evidence failure must NOT silently return empty success —
    the user needs to know the system couldn't find anything (vs found
    nothing)."""
    class _OkEmbedding:
        model = "fake"
        dim = 4
        def embed_one(self, text): return [0.1] * 4
        def embed_texts(self, texts):
            from types import SimpleNamespace
            return SimpleNamespace(embeddings=[[0.1] * 4] * len(texts))

    monkeypatch.setattr(retrieval_service, "get_embedding_gateway", lambda: _OkEmbedding())
    monkeypatch.setattr(retrieval_service, "milvus_client", _FakeMilvusBoom())

    resp = retrieval_service.find_counter_evidence(
        "ws-1", "claim", top_k=10,
        use_reranker=False, use_judge=False,
    )
    assert resp.status == "failed"
    assert resp.total == 0
    assert resp.diagnostic_code == "milvus_unavailable"


def test_reranker_failure_falls_back_to_score_only(
    monkeypatch, fake_milvus, fake_embedding
) -> None:
    """Reranker is best-effort: a failure degrades to vector-score ordering.
    This is documented behaviour — semantic_search returns degraded with
    the available signal. (Counter Evidence does the same.)"""
    class _BoomReranker:
        def rerank(self, query, documents, *, top_n):
            raise RuntimeError("reranker 502")

    fake_milvus.search.return_value = [
        {"chunk_id": f"c{i}", "workspace_id": "ws-1", "paper_id": "p-A",
         "section": "M", "text": f"t{i}", "score": 0.9 - i * 0.1,
         "source_artifact_id": "a1", "chunk_index": i}
        for i in range(3)
    ]
    monkeypatch.setattr(retrieval_service, "get_reranker_gateway", lambda: _BoomReranker())

    resp = retrieval_service.semantic_search("ws-1", "query", top_k=3, use_reranker=True)
    assert resp.status == "degraded"
    assert resp.diagnostic_code == "reranker_degraded"
    # We got the available signal, not a fake failure.
    assert resp.total == 3
    assert all(item.retrieval_stage == "candidate_recall" for item in resp.items)


def test_semantic_search_can_diversify_reranked_chunks_by_paper(
    monkeypatch, fake_milvus, fake_embedding
) -> None:
    """Chat answer generation should not let one paper occupy every evidence slot."""

    class _OrderedReranker:
        def __init__(self):
            self.top_n = None

        def rerank(self, query, documents, *, top_n):
            self.top_n = top_n
            return type("Result", (), {
                "hits": [
                    type("Hit", (), {"index": index, "relevance_score": 1.0 - index * 0.01})()
                    for index in range(len(documents))
                ]
            })()

    reranker = _OrderedReranker()
    fake_milvus.search.return_value = [
        {"chunk_id": "a-1", "workspace_id": "ws-1", "paper_id": "paper-a", "section": "Method", "text": "a1", "score": 0.99, "source_artifact_id": "art-a", "chunk_index": 1},
        {"chunk_id": "a-2", "workspace_id": "ws-1", "paper_id": "paper-a", "section": "Method", "text": "a2", "score": 0.98, "source_artifact_id": "art-a", "chunk_index": 2},
        {"chunk_id": "a-3", "workspace_id": "ws-1", "paper_id": "paper-a", "section": "Result", "text": "a3", "score": 0.97, "source_artifact_id": "art-a", "chunk_index": 3},
        {"chunk_id": "b-1", "workspace_id": "ws-1", "paper_id": "paper-b", "section": "Method", "text": "b1", "score": 0.96, "source_artifact_id": "art-b", "chunk_index": 1},
        {"chunk_id": "c-1", "workspace_id": "ws-1", "paper_id": "paper-c", "section": "Method", "text": "c1", "score": 0.95, "source_artifact_id": "art-c", "chunk_index": 1},
    ]
    monkeypatch.setattr(retrieval_service, "get_reranker_gateway", lambda: reranker)

    response = retrieval_service.semantic_search(
        "ws-1",
        "query",
        top_k=3,
        diversify_by_paper=True,
    )

    assert response.status == "succeeded"
    assert [item.paper_id for item in response.items] == ["paper-a", "paper-b", "paper-c"]
    assert response.filters_applied["diversify_by_paper"] is True
    assert reranker.top_n == 9


# ==================================================================
# 6. Paper.chunk_count vs Milvus state — no false projection
# ==================================================================


def test_paper_chunk_count_matches_indexed_chunks(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """The Task row + Paper.chunk_count must reflect what was actually
    indexed, not a stale count from a previous indexing run.

    This test asserts the indexer reports the right counts. The pipeline
    layer that updates paper.chunk_count from index_paper_chunks is
    verified separately by the end-to-end integration test in
    test_parse_pipeline.py — here we lock down the indexer's contract."""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=4)

    fake_milvus.get_existing_chunk_ids.return_value = set()
    fake_milvus.insert_chunks.return_value = 4  # batch returns count

    result = retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert result.total_chunks == 4  # chunk_index Artifact read
    assert result.indexed_count == 4  # Milvus insert returned count
    assert result.skipped_count == 0


def test_partial_insertion_reported_as_partial(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """If Milvus insert_chunks returns fewer than the batch size, the
    indexer must report it honestly. This is the foundation for the
    Task row's accuracy (Task reports partial completion, not 100%)."""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=10)

    fake_milvus.get_existing_chunk_ids.return_value = set()
    # Single batch (10 chunks < batch_size=100): 8 of 10 inserted.
    fake_milvus.insert_chunks.side_effect = lambda batch: 8

    result = retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)
    assert result.total_chunks == 10
    assert result.indexed_count == 8  # honest count
    # Caller can compare indexed_count vs total_chunks to detect partial.


# ==================================================================
# 7. Fresh-DB end-to-end (lightweight contract check)
# ==================================================================


def test_indexer_writes_consistent_records_to_milvus(
    db_session, fake_milvus, fake_embedding, tmp_path, monkeypatch
) -> None:
    """Full indexing → record-shape contract: every record passed to
    Milvus must carry workspace_id, paper_id, chunk_id, section,
    text, embedding. The pipeline that calls this depends on every
    field landing in the vector store correctly."""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)
    _write_chunks_jsonl(db_session, paper, n=2)

    fake_milvus.get_existing_chunk_ids.return_value = set()
    retrieval_service.index_paper_chunks(ws.id, paper.id, db=db_session)

    # Collect all records passed to insert_chunks across batches.
    all_records: list[dict] = []
    for call in fake_milvus.insert_chunks.call_args_list:
        all_records.extend(call.args[0])

    assert len(all_records) == 2
    for record in all_records:
        assert record["workspace_id"] == ws.id
        assert record["paper_id"] == paper.id
        assert record["chunk_id"].startswith(paper.id + "-c")
        assert record["section"]  # default "Unknown" if chunk had none
        assert isinstance(record["embedding"], list)
        assert len(record["embedding"]) == 4  # matches fake_embedding.dim


# ==================================================================
# Auxiliary: paper.soft_delete failure path — Milvus exception must NOT
# silently leave the paper's vectors indexed
# ==================================================================


def test_paper_soft_delete_records_failure_when_milvus_throws(
    db_session, monkeypatch
) -> None:
    """If Milvus delete_by_paper fails, the paper soft_delete must raise so
    the API returns 5xx (NOT a silent 200). The DB-level is_deleted flip is
    committed before the Milvus call — leaving a known-inconsistent state
    that the API caller can detect via a follow-up GET. We choose fail-loud
    over silent inconsistency.
    """
    fake_milvus = MagicMock(name="milvus_client")
    fake_milvus.delete_by_paper.side_effect = RuntimeError("milvus unreachable")
    import app.domains.paper.service as paper_service_module
    monkeypatch.setattr(paper_service_module.milvus_client, "delete_by_paper", fake_milvus.delete_by_paper)

    ws = _workspace(db_session)
    paper = _paper(db_session, ws.id)

    with pytest.raises(RuntimeError, match="milvus unreachable"):
        PaperService(db_session).soft_delete(paper.id)

    # DB-side flag IS flipped (documented; a follow-up reconcile can repair).
    db_session.expire_all()
    fresh = db_session.get(Paper, paper.id)
    assert fresh is not None
    assert fresh.is_deleted is True
