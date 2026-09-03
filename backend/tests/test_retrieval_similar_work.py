"""Similar Work paper-level aggregation tests (RG-4 / D2).

The pipeline over-fetches from Milvus, then:
  1. drops chunks in low-value sections (References / Acknowledgments etc.)
  2. groups hits by paper and caps each paper at SIMILAR_WORK_MAX_CHUNKS_PER_PAPER
  3. reranks the diversified candidate pool

These tests mock Milvus/embedding and verify the post-aggregation shape
without needing a live Milvus instance.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from app.core.config import settings
from app.domains.artifact.service import ArtifactService
from app.domains.paper.models import Paper
from app.domains.retrieval import service
from app.domains.retrieval.service import (
    LOW_VALUE_SECTIONS,
    SIMILAR_WORK_MAX_CHUNKS_PER_PAPER,
    _hit_to_result_item,
    _hybrid_rerank_top_k,
    _is_low_value_section,
    _paper_max_top_k,
)


# ==================================================================
# _paper_max_top_k (paper-level dedup for the final top-k)
# ==================================================================


def _rit(hit_id: str, paper_id: str | None, score: float):
    return _hit_to_result_item({
        "chunk_id": hit_id,
        "paper_id": paper_id,
        "score": score,
        "text": f"text-{hit_id}",
        "section": "Method",
    })


def test_paper_max_keeps_top_chunk_per_paper() -> None:
    items = [
        _rit("a1", "p-a", 0.9),
        _rit("a2", "p-a", 0.8),
        _rit("b1", "p-b", 0.7),
        _rit("c1", "p-c", 0.6),
    ]
    out = _paper_max_top_k(items, 10)
    assert [item.paper_id for item in out] == ["p-a", "p-b", "p-c"]
    assert out[0].score == 0.9  # kept the higher-scoring chunk of p-a


def test_paper_max_limits_to_top_k_distinct_papers() -> None:
    items = [_rit(f"c{i}", f"p-{i}", 1.0 - i * 0.01) for i in range(15)]
    out = _paper_max_top_k(items, 10)
    assert len(out) == 10
    assert len({item.paper_id for item in out}) == 10


def test_paper_max_orders_by_best_score_desc() -> None:
    items = [
        _rit("a1", "p-a", 0.5),
        _rit("b1", "p-b", 0.9),
        _rit("c1", "p-c", 0.7),
    ]
    out = _paper_max_top_k(items, 10)
    assert [item.paper_id for item in out] == ["p-b", "p-c", "p-a"]


def test_paper_max_keeps_paperless_to_fill_remaining_slots() -> None:
    items = [_rit("a1", "p-a", 0.9), _rit("n1", None, 0.5)]
    out = _paper_max_top_k(items, 3)
    assert len(out) == 2
    assert out[-1].paper_id is None


def test_paper_max_empty_input() -> None:
    assert _paper_max_top_k([], 10) == []


# ==================================================================
# _hybrid_rerank_top_k (raw + rerank blend for similar work)
# ==================================================================


def test_hybrid_keeps_high_raw_low_rerank_paper_in_contention() -> None:
    # p-a: high raw (0.9) but demoted by the cross-encoder; p-b the reverse.
    candidates = [
        {"chunk_id": "a1", "paper_id": "p-a", "score": 0.9, "text": "t", "section": "Method"},
        {"chunk_id": "b1", "paper_id": "p-b", "score": 0.6, "text": "t", "section": "Method"},
    ]
    reranked = [_rit("b1", "p-b", 0.99), _rit("a1", "p-a", 0.30)]
    out = _hybrid_rerank_top_k(candidates, reranked, 2)
    assert {item.paper_id for item in out} == {"p-a", "p-b"}


def test_hybrid_dedupes_paper_chunks() -> None:
    candidates = [
        {"chunk_id": "a1", "paper_id": "p-a", "score": 0.9, "text": "t", "section": "Method"},
        {"chunk_id": "a2", "paper_id": "p-a", "score": 0.8, "text": "t", "section": "Method"},
        {"chunk_id": "c1", "paper_id": "p-c", "score": 0.7, "text": "t", "section": "Method"},
    ]
    reranked = [_rit("a2", "p-a", 0.9), _rit("a1", "p-a", 0.85), _rit("c1", "p-c", 0.7)]
    out = _hybrid_rerank_top_k(candidates, reranked, 2)
    assert len(out) == 2
    assert {item.paper_id for item in out} == {"p-a", "p-c"}


def test_hybrid_empty_input() -> None:
    assert _hybrid_rerank_top_k([], [], 10) == []


# ==================================================================
# _is_low_value_section
# ==================================================================


def test_low_value_section_matches_references_case_insensitive() -> None:
    assert _is_low_value_section("References")
    assert _is_low_value_section("references")
    assert _is_low_value_section("REFERENCES")
    assert _is_low_value_section("References ")  # trailing space
    assert _is_low_value_section("  References  ")


def test_low_value_section_matches_other_citation_sections() -> None:
    for section in ("Bibliography", "Acknowledgments", "Acknowledgements",
                    "Appendix", "Author Contributions", "Supplementary Material"):
        assert _is_low_value_section(section), section


def test_low_value_section_passes_through_normal_sections() -> None:
    for section in ("Method", "Methods", "Methodology", "Introduction",
                    "Related Work", "Experiments", "Results",
                    "Discussion", "Conclusion", None, ""):
        assert not _is_low_value_section(section), section


def test_low_value_sections_is_explicit() -> None:
    # Snapshot: if we ever grow this list, we want a clear diff. Stable set of
    # currently-recognised low-value sections.
    assert LOW_VALUE_SECTIONS == frozenset({
        "references",
        "bibliography",
        "acknowledgments",
        "acknowledgements",
        "appendix",
        "author contributions",
        "supplementary",
        "supplementary material",
    })


# ==================================================================
# Service-level: paper aggregation + low-value filter
# ==================================================================


class _FakeEmbedding:
    def embed_one(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    def embed_texts(self, texts: list[str]):
        from types import SimpleNamespace
        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3, 0.4]] * len(texts))


class _FakeMilvus:
    """Replaces service.milvus_client. Returns hits verbatim per call (no dedup
    or filtering — that happens in the aggregation step we're testing)."""

    def __init__(self, hits: list[dict]) -> None:
        self.hits = hits

    def search(self, query_vector, workspace_id, top_k=10, *, paper_id=None, exclude_paper_ids=None, section=None):
        return list(self.hits)


def _patch(monkeypatch, hits: list[dict]) -> None:
    """Patch milvus_client.search + embedding + reranker so we only exercise the aggregation step."""
    monkeypatch.setattr(service, "milvus_client", _FakeMilvus(hits))
    monkeypatch.setattr(service, "get_embedding_gateway", _FakeEmbedding)

    # Bypass the reranker with a deterministic no-op that preserves input order.
    class _NoopReranker:
        def rerank(self, query, documents, *, top_n):
            from types import SimpleNamespace
            return SimpleNamespace(
                hits=[
                    SimpleNamespace(index=i, relevance_score=1.0 - i * 0.01)
                    for i in range(min(len(documents), top_n))
                ]
            )

    monkeypatch.setattr(service, "get_reranker_gateway", lambda: _NoopReranker())


@pytest.fixture
def source_paper(db_session, tmp_path, monkeypatch):
    """Create a real paper whose source chunks live in a storage Artifact."""
    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path / "storage"))
    from app.domains.workspace.models import Workspace

    workspace = Workspace(id=str(uuid4()), name="Similar Work", is_deleted=False)
    paper = Paper(
        id=str(uuid4()),
        workspace_id=workspace.id,
        title="Source paper",
        authors=[],
        source="manual",
        is_deleted=False,
    )
    db_session.add_all([workspace, paper])
    db_session.flush()
    payload = "\n".join(
        json.dumps({
            "chunk_id": f"src-{i}",
            "workspace_id": workspace.id,
            "paper_id": paper.id,
            "source_artifact_id": "art-1",
            "chunk_index": i,
            "text": f"source chunk {i}",
            "start_char": 0,
            "end_char": 10,
        })
        for i in range(3)
    )
    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace.id,
        filename=f"{paper.id}_chunks.jsonl",
        content=payload.encode("utf-8"),
        mime_type="application/jsonl",
        kind="chunk_index",
    )
    paper.chunk_index_artifact_id = artifact.id
    db_session.commit()
    return workspace, paper


def _hit(chunk_id: str, paper_id: str, *, section: str | None = "Method", score: float = 0.5) -> dict:
    return {
        "chunk_id": chunk_id,
        "workspace_id": "ws-1",
        "paper_id": paper_id,
        "source_artifact_id": "art-x",
        "chunk_index": 1,
        "section": section,
        "text": f"text {chunk_id}",
        "score": score,
    }


def test_similar_work_drops_low_value_sections(monkeypatch, db_session, source_paper) -> None:
    workspace, paper = source_paper
    hits = [
        _hit("c1", "p-other", section="Method", score=0.9),
        _hit("c2", "p-other", section="References", score=0.95),  # should drop
        _hit("c3", "p-other", section="Acknowledgments", score=0.99),  # should drop
    ]
    _patch(monkeypatch, hits)

    resp = service.find_similar_work(
        workspace.id,
        paper.id,
        top_k=10,
        db=db_session,
        use_reranker=True,
    )
    assert len(resp.items) == 1
    assert resp.items[0].chunk_id == "c1"
    assert resp.filters_applied["low_value_section_filter"] is True


def test_similar_work_caps_chunks_per_paper(monkeypatch, db_session, source_paper) -> None:
    workspace, paper = source_paper
    # p-A has 4 high-quality hits, p-B has 1. Without cap, p-A dominates.
    hits = [
        _hit(f"a{i}", "p-A", score=0.9 - i * 0.05) for i in range(4)
    ] + [_hit("b1", "p-B", score=0.7)]
    _patch(monkeypatch, hits)

    resp = service.find_similar_work(
        workspace.id,
        paper.id,
        top_k=10,
        db=db_session,
        use_reranker=True,
    )
    # With cap=2: 2 from p-A + 1 from p-B = 3
    by_paper: dict[str, int] = {}
    for item in resp.items:
        by_paper[item.paper_id or ""] = by_paper.get(item.paper_id or "", 0) + 1
    assert by_paper.get("p-A", 0) <= SIMILAR_WORK_MAX_CHUNKS_PER_PAPER
    assert "p-B" in by_paper  # diversity preserved
    assert resp.filters_applied["max_chunks_per_paper"] == SIMILAR_WORK_MAX_CHUNKS_PER_PAPER


def test_similar_work_falls_back_when_all_low_value(monkeypatch, db_session, source_paper) -> None:
    """If every candidate is a low-value section chunk, return them anyway
    rather than an empty Top-K (the section classifier can fail)."""
    workspace, paper = source_paper
    hits = [
        _hit("r1", "p-A", section="References", score=0.9),
        _hit("r2", "p-A", section="References", score=0.8),
    ]
    _patch(monkeypatch, hits)

    resp = service.find_similar_work(
        workspace.id,
        paper.id,
        top_k=10,
        db=db_session,
        use_reranker=True,
    )
    # Both chunks come from the same paper → paper-level dedup keeps one result
    # (the fallback guarantees a NON-empty Top-K, not per-chunk results).
    assert len(resp.items) == 1
    assert resp.items[0].paper_id == "p-A"


def test_similar_work_paper_diversity(monkeypatch, db_session, source_paper) -> None:
    """Over-fetch + per-paper cap should keep Top-10 paper-diverse when the
    corpus supports it (no single paper can occupy >50% of Top-10)."""
    workspace, paper = source_paper
    # 10 distinct papers, 3 chunks each → after cap (2/paper), each contributes 2.
    hits = []
    for p_idx in range(10):
        for c_idx in range(3):
            hits.append(_hit(f"p{p_idx}-c{c_idx}", f"p-{p_idx}", score=0.9 - p_idx * 0.05 - c_idx * 0.01))
    _patch(monkeypatch, hits)

    resp = service.find_similar_work(
        workspace.id,
        paper.id,
        top_k=10,
        db=db_session,
        use_reranker=True,
    )
    assert len(resp.items) == 10
    distinct_papers = len({item.paper_id for item in resp.items})
    # Without cap, the highest-scoring paper alone could fill Top-10.
    assert distinct_papers >= 5  # at least half the Top-10 are distinct papers
    # Source paper must not appear.
    assert paper.id not in {item.paper_id for item in resp.items}


def test_similar_work_source_paper_still_excluded(monkeypatch, db_session, source_paper) -> None:
    workspace, paper = source_paper
    # Even if Milvus returned the source's chunks somehow, the source is
    # excluded by the service contract.
    hits = [
        _hit("src1", paper.id, score=0.99),  # MUST NOT appear
        _hit("o1", "p-other", score=0.5),
    ]
    _patch(monkeypatch, hits)

    resp = service.find_similar_work(
        workspace.id,
        paper.id,
        top_k=10,
        db=db_session,
        use_reranker=True,
    )
    assert all(item.paper_id != paper.id for item in resp.items)
    assert any(item.paper_id == "p-other" for item in resp.items)
    assert paper.id in resp.filters_applied["excluded_paper_ids"]
