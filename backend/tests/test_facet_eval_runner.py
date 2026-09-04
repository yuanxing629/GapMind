"""只读 facet 评测运行器契约测试。"""

from __future__ import annotations

from evaluation.retrieval.run_chat_facet_ab import _merge_items
from evaluation.retrieval.run_fixed_semantic_facet_ab import _metrics, _status
from app.domains.retrieval.schemas import RetrievalResponse
from app.domains.retrieval.schemas import RetrievalResultItem


def test_failed_metrics_use_requested_top_k_key() -> None:
    response = RetrievalResponse(
        workspace_id="workspace-1",
        status="failed",
        items=[],
        total=0,
    )

    metrics = _metrics(None, "workspace-1", response, "paper-1", 15)

    assert metrics["recall@15"] is None
    assert metrics["mrr@15"] is None
    assert "recall@10" not in metrics


def test_facet_status_preserves_primary_failure_and_facet_degradation() -> None:
    primary = RetrievalResponse(workspace_id="workspace-1", status="succeeded")
    failed_primary = RetrievalResponse(workspace_id="workspace-1", status="failed")
    degraded_facet = RetrievalResponse(workspace_id="workspace-1", status="degraded")

    assert _status([degraded_facet], primary) == "degraded"
    assert _status([], failed_primary) == "failed"


def test_facet_merge_deduplicates_chunks_then_keeps_one_paper() -> None:
    first = RetrievalResultItem(
        paper_id="paper-1", chunk_id="chunk-1", score=0.7, text="first"
    )
    same_chunk_better = RetrievalResultItem(
        paper_id="paper-1", chunk_id="chunk-1", score=0.9, text="better"
    )
    second_chunk_same_paper = RetrievalResultItem(
        paper_id="paper-1", chunk_id="chunk-2", score=0.8, text="second"
    )
    other_paper = RetrievalResultItem(
        paper_id="paper-2", chunk_id="chunk-3", score=0.6, text="other"
    )

    merged = _merge_items(
        [
            RetrievalResponse(workspace_id="workspace-1", items=[first, second_chunk_same_paper]),
            RetrievalResponse(workspace_id="workspace-1", items=[same_chunk_better, other_paper]),
        ],
        top_k=3,
    )

    assert [(item.paper_id, item.chunk_id, item.score) for item in merged] == [
        ("paper-1", "chunk-1", 0.9),
        ("paper-2", "chunk-3", 0.6),
    ]
