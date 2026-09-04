"""确定性 Chat retrieval facet 规划测试。"""

from __future__ import annotations

from types import SimpleNamespace

from app.domains.chat.retrieval_facets import (
    MAX_RETRIEVAL_FACETS,
    plan_retrieval_facets,
)
from app.domains.retrieval.schemas import RetrievalResultItem


def test_formula_facet_preserves_the_normalized_primary_question() -> None:
    facets = plan_retrieval_facets("  GIB 的优化目标，分析一下它的公式  ")

    assert [facet.name for facet in facets] == ["formula"]
    assert facets[0].query.startswith("gib 的优化目标，分析一下它的公式")
    assert "formula equation loss objective formulation" in facets[0].query
    assert facets[0].section_hints == ("Method", "Related Work")
    assert "优化目标" in facets[0].matched_triggers
    assert "公式" in facets[0].matched_triggers


def test_method_and_comparison_facets_have_stable_order_and_limit() -> None:
    facets = plan_retrieval_facets("比较经典方法与基线的机制、实验结果和公式")

    assert len(facets) == MAX_RETRIEVAL_FACETS
    assert [facet.name for facet in facets] == ["formula", "method"]
    assert all("比较经典方法与基线的机制、实验结果和公式" in facet.query for facet in facets)


def test_dataset_and_comparison_facets_are_distinct() -> None:
    facets = plan_retrieval_facets("比较两个模型在 Cora 数据集上的实验结果")

    assert [facet.name for facet in facets] == ["dataset", "comparison"]
    assert facets[0].section_hints == ("Experiment", "Method")
    assert facets[1].section_hints == ("Experiment", "Related Work")


def test_stability_question_without_facet_terms_returns_no_facet() -> None:
    assert plan_retrieval_facets("分布偏移下 GNN 解释的稳定性如何？") == ()


def test_english_word_matching_does_not_match_longer_words() -> None:
    assert plan_retrieval_facets("methodology overview") == ()
    assert [facet.name for facet in plan_retrieval_facets("method overview")] == ["method"]


def test_empty_question_returns_no_facet() -> None:
    assert plan_retrieval_facets(" \n\t ") == ()


def test_eval_item_snapshot_preserves_offsets_without_exporting_text(monkeypatch) -> None:
    from evaluation.retrieval import run_chat_facet_ab

    monkeypatch.setattr(
        run_chat_facet_ab,
        "find_chunk_record",
        lambda workspace_id, paper_id, chunk_id, *, db: SimpleNamespace(
            workspace_id=workspace_id,
            paper_id=paper_id,
            section="Method",
            chunk_index=4,
            start_char=120,
            end_char=240,
        ),
    )
    item = RetrievalResultItem(
        paper_id="paper-1",
        artifact_id="artifact-1",
        chunk_id="chunk-1",
        section="Unknown",
        text="private retrieved text",
        score=0.81234567,
    )

    snapshot = run_chat_facet_ab._item_snapshot(None, "workspace-1", item)

    assert snapshot["section"] == "Method"
    assert snapshot["chunk_index"] == 4
    assert snapshot["start_char"] == 120
    assert snapshot["end_char"] == 240
    assert snapshot["chunk_record_resolved"] is True
    assert "text" not in snapshot
