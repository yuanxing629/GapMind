"""Counter Evidence 角色排序与 Judge 约束测试（RG-5 / D3）。

覆盖：
  * ``judgement`` 字段是 Literal（拒绝词汇表之外的字符串）
  * ``_diversify_and_sort_counter_items`` 按角色优先级排序并限制单论文分块
  * ``find_counter_evidence`` 正确填充 ``empty_reason``，覆盖三种空状态
   （retrieval_empty / judge_failed / genuinely_no_counter_evidence）
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.retrieval import service
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem
from app.domains.retrieval.service import (
    COUNTER_EVIDENCE_MAX_CHUNKS_PER_PAPER,
    COUNTER_ROLE_PRIORITY,
    _diversify_and_sort_counter_items,
)


# ==================================================================
# Schema validation：judgement 是 Literal
# ==================================================================


def test_judgement_accepts_all_vocabulary_values() -> None:
    for role in ("contradicts", "qualifies", "supports", "overlaps", "unknown"):
        item = RetrievalResultItem(judgement=role)
        assert item.judgement == role


def test_judgement_rejects_unknown_role_string() -> None:
    with pytest.raises(ValueError):
        RetrievalResultItem.model_validate(
            {"judgement": "maybe_contradicts"},
        )


def test_empty_reason_is_constrained_to_three_values() -> None:
    with pytest.raises(ValueError):
        RetrievalResponse.model_validate(
            {"workspace_id": "ws-1", "purpose": "counter_evidence",
             "empty_reason": "maybe_empty"},
        )


# ==================================================================
# 纯函数：_diversify_and_sort_counter_items
# ==================================================================


def _item(
    chunk_id: str,
    paper_id: str,
    judgement: str = "qualifies",
    confidence: float = 0.5,
    score: float = 0.5,
) -> RetrievalResultItem:
    return RetrievalResultItem(
        result_id=f"r-{chunk_id}",
        chunk_id=chunk_id,
        paper_id=paper_id,
        workspace_id="ws-1",
        score=score,
        judgement=judgement,
        judgement_confidence=confidence,
    )


def test_sort_contradicts_before_qualifies_before_supports_before_unknown() -> None:
    items = [
        _item("c", "p-A", "supports", confidence=0.99),
        _item("u", "p-B", "unknown", confidence=0.0),
        _item("q", "p-C", "qualifies", confidence=0.99),
        _item("x", "p-D", "contradicts", confidence=0.5),
    ]
    ranked = _diversify_and_sort_counter_items(items)
    assert [i.chunk_id for i in ranked] == ["x", "q", "c", "u"]


def test_sort_within_role_higher_confidence_first() -> None:
    items = [
        _item("lo", "p-A", "qualifies", confidence=0.5),
        _item("hi", "p-A", "qualifies", confidence=0.9),
        _item("md", "p-A", "qualifies", confidence=0.7),
    ]
    ranked = _diversify_and_sort_counter_items(items)
    assert [i.chunk_id for i in ranked] == ["hi", "md", "lo"]


def test_cap_drops_extra_chunks_from_same_paper() -> None:
    items = [
        _item(f"c{i}", "p-A", "contradicts", confidence=0.9 - i * 0.01)
        for i in range(5)
    ] + [
        _item("other", "p-B", "contradicts", confidence=0.85),
    ]
    ranked = _diversify_and_sort_counter_items(items)
    by_paper = [i.paper_id for i in ranked]
    assert by_paper.count("p-A") == COUNTER_EVIDENCE_MAX_CHUNKS_PER_PAPER
    # p-A 保留置信度最高的分块（优先排序），而不是任意分块。
    kept_a = [i for i in ranked if i.paper_id == "p-A"]
    assert [i.chunk_id for i in kept_a] == ["c0", "c1", "c2"]
    # p-B 仍然保留，论文多样性得到保持。
    assert any(i.paper_id == "p-B" for i in ranked)


def test_sort_empty_list_returns_empty() -> None:
    assert _diversify_and_sort_counter_items([]) == []


def test_role_priority_table_lists_user_facing_roles() -> None:
    # 固定面向用户的排序快照，避免之后静默改变顺序。
    assert list(COUNTER_ROLE_PRIORITY.keys()) == [
        "contradicts", "qualifies", "supports", "overlaps", "unknown",
    ]


# ==================================================================
# find_counter_evidence：empty_reason 分类
# ==================================================================


class _FakeEmbedding:
    def embed_one(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    def embed_texts(self, texts: list[str]):
        from types import SimpleNamespace
        return SimpleNamespace(embeddings=[[0.1, 0.2, 0.3, 0.4]] * len(texts))


class _FakeMilvus:
    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self.hits = hits

    def search(self, query_vector, workspace_id, top_k=10, *, paper_id=None, exclude_paper_ids=None, section=None):
        return list(self.hits)


def _patch(monkeypatch, *, milvus_hits, judge_hits, rerank_hits=None):
    """替换 Milvus、向量生成、重排器和判断器，返回受控数据。

    rerank_hits 是 ``(index, score)`` 对列表，默认“保持顺序”。
    judge_hits 是按索引对齐的 ``(judgement, confidence)`` 对列表。
    如果禁用重排，则按 milvus_hits 的顺序处理。
    """
    monkeypatch.setattr(service, "milvus_client", _FakeMilvus(milvus_hits))
    monkeypatch.setattr(service, "get_embedding_gateway", _FakeEmbedding)

    if rerank_hits is None:
        rerank_hits = [(i, 1.0 - i * 0.01) for i in range(len(milvus_hits))]

    class _NoopReranker:
        def rerank(self, query, documents, *, top_n):
            from types import SimpleNamespace
            return SimpleNamespace(
                hits=[
                    SimpleNamespace(index=idx, relevance_score=score)
                    for idx, score in rerank_hits[:top_n]
                ]
            )

    monkeypatch.setattr(service, "get_reranker_gateway", lambda: _NoopReranker())

    class _FakeJudge:
        def judge_batch(self, claim, passages, *, max_passages):
            from app.gateway.judge import JudgementHit, JudgementResult
            from types import SimpleNamespace
            return JudgementResult(
                hits=[
                    JudgementHit(index=i, judgement=role, confidence=conf)
                    for i, (role, conf) in enumerate(judge_hits[:max_passages])
                ]
            )

    monkeypatch.setattr(service, "get_judgement_gateway", _FakeJudge)


def _hit(chunk_id: str, paper_id: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "chunk_id": chunk_id,
        "workspace_id": "ws-1",
        "paper_id": paper_id,
        "source_artifact_id": "art-x",
        "chunk_index": 1,
        "section": "Method",
        "text": f"text {chunk_id}",
        "score": 0.5,
    }
    base.update(overrides)
    return base


def test_empty_reason_retrieval_empty_when_no_milvus_hits(monkeypatch) -> None:
    _patch(monkeypatch, milvus_hits=[], judge_hits=[])
    resp = service.find_counter_evidence(
        "ws-1", "claim", top_k=10, use_reranker=True, use_judge=True,
        exclude_paper_ids={"src"},
    )
    assert resp.total == 0
    assert resp.empty_reason == "retrieval_empty"
    assert resp.status == "succeeded"  # empty is NOT a failure


def test_empty_reason_judge_failed_when_all_candidates_unknown(monkeypatch) -> None:
    _patch(
        monkeypatch,
        milvus_hits=[_hit("c1", "p-A"), _hit("c2", "p-B")],
        # 每个 Judge 结果都是失败哨兵。
        judge_hits=[("unknown", 0.0), ("unknown", 0.0)],
    )
    resp = service.find_counter_evidence(
        "ws-1", "claim", top_k=10, use_reranker=True, use_judge=True,
        exclude_paper_ids={"src"},
    )
    # 两个条目都通过论文上限（2 个分块、2 篇论文），然后排序
    #（都是 unknown/0.0 → 按 score 打破平局）。
    assert resp.total == 2
    assert resp.empty_reason == "judge_failed"
    assert resp.status == "degraded"


def test_empty_reason_genuinely_no_counter_evidence_when_only_supports_overlaps(monkeypatch) -> None:
    """判断器已运行，所有分块都被判断为 supports/overlaps（没有 contradicts / qualifies）。

    系统应说明“找到了相关工作，但没有内容反驳该主张”，而不是说“无法判断找到的内容”。"""
    _patch(
        monkeypatch,
        milvus_hits=[_hit("c1", "p-A"), _hit("c2", "p-B")],
        judge_hits=[("supports", 0.8), ("overlaps", 0.7)],
    )
    resp = service.find_counter_evidence(
        "ws-1", "claim", top_k=10, use_reranker=True, use_judge=True,
        exclude_paper_ids={"src"},
    )
    # 单论文上限（3）未触发，总共只有 2 个分块。
    assert resp.total == 2
    assert resp.empty_reason == "genuinely_no_counter_evidence"
    assert resp.status == "succeeded"  # judge worked fine, just no counter


def test_non_empty_items_dont_set_empty_reason(monkeypatch) -> None:
    """如果有任何 contradicts/qualifies 项保留下来，响应就不为空，且 empty_reason 为 null。"""
    _patch(
        monkeypatch,
        milvus_hits=[_hit("c1", "p-A"), _hit("c2", "p-B")],
        judge_hits=[("contradicts", 0.9), ("supports", 0.8)],
    )
    resp = service.find_counter_evidence(
        "ws-1", "claim", top_k=10, use_reranker=True, use_judge=True,
        exclude_paper_ids={"src"},
    )
    assert resp.total >= 1
    assert resp.empty_reason is None
    # 无论输入顺序如何，contradicts 都排在最前（优先级 0）。
    assert resp.items[0].judgement == "contradicts"


def test_role_priority_is_enforced_against_reranker_score(monkeypatch) -> None:
    """即使重排器给某个 supports 分块很高的分数，contradicts 分块也必须排在前面。

    面向用户的信号优先。"""
    _patch(
        monkeypatch,
        milvus_hits=[_hit("supports1", "p-Sup"), _hit("contra1", "p-Con")],
        # p-Sup 的 reranker 分数高很多；p-Con 较低。
        rerank_hits=[(0, 0.99), (1, 0.10)],
        judge_hits=[("supports", 0.99), ("contradicts", 0.99)],
    )
    resp = service.find_counter_evidence(
        "ws-1", "claim", top_k=10, use_reranker=True, use_judge=True,
        exclude_paper_ids={"src"},
    )
    assert resp.items[0].chunk_id == "contra1"  # role priority wins
    assert resp.items[1].chunk_id == "supports1"
