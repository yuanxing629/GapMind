"""P0 精确去重与 P1 语义去重单元测试（extraction/dedup.py）。"""

from __future__ import annotations

from app.workers.tasks.extraction.dedup import (
    content_signature,
    dedup_exact,
    dedup_semantic,
    semantic_text,
)


def _item(
    *,
    type_: str = "claim",
    name: str = "A claim",
    statement: str = "the same claim statement",
    paper_id: str = "p-1",
    start: int = 100,
    end: int = 200,
    confidence: float = 0.8,
) -> dict:
    return {
        "type": type_,
        "canonical_name": name,
        "confidence": confidence,
        "content": {"statement": statement} if type_ == "claim" else {"description": statement},
        "source_provenance": {
            "paper_id": paper_id,
            "artifact_id": f"art-{paper_id}",
            "start_char": start,
            "end_char": end,
            "batch_index": 0,
        },
        "evidence_text": statement,
    }


# ------------------------------------------------------------- content_signature：内容签名
def test_content_signature_uses_statement() -> None:
    assert content_signature({"statement": "GraphRAG is better"}) == content_signature(
        {"statement": "  GraphRAG is better  "}
    )


def test_content_signature_uses_description_for_limitation() -> None:
    assert content_signature({"description": "KG is incomplete"}) == content_signature(
        {"description": "kg is incomplete"}
    )


def test_content_signature_empty_returns_stable() -> None:
    assert content_signature({}) == content_signature(None)
    assert content_signature({}) == content_signature({"statement": None})


# ------------------------------------------------------------- 规则 1：精确重复
def test_exact_duplicate_keeps_first_rejects_rest() -> None:
    a = _item(statement="same fact", start=100, end=200, confidence=0.7)
    b = _item(statement="same fact", start=100, end=200, confidence=0.9)  # same everything
    survivors, rejected = dedup_exact([a, b])
    assert survivors == [a]
    assert rejected == [b]


def test_same_span_different_content_both_survive() -> None:
    a = _item(statement="claim one", start=100, end=200)
    b = _item(statement="claim two", start=100, end=200)  # same span, different fact
    survivors, rejected = dedup_exact([a, b])
    assert len(survivors) == 2
    assert rejected == []


def test_method_exact_duplicate_is_deduped() -> None:
    a = _item(type_="method", statement="PGIB is a framework", start=10, end=50)
    b = _item(type_="method", statement="PGIB is a framework", start=10, end=50)
    survivors, rejected = dedup_exact([a, b])
    assert len(survivors) == 1
    assert len(rejected) == 1


# ------------------------------------------------------------- 规则 2：跨类型
def test_claim_and_limitation_same_span_keeps_higher_confidence() -> None:
    claim = _item(type_="claim", statement="position bias is present", start=7082, end=7323, confidence=0.9)
    limitation = _item(type_="limitation", statement="position bias is present", start=7082, end=7323, confidence=0.7)
    survivors, rejected = dedup_exact([claim, limitation])
    assert survivors == [claim]
    assert rejected == [limitation]


def test_claim_and_limitation_keeps_higher_even_if_second() -> None:
    limitation = _item(type_="limitation", statement="position bias is present", start=7082, end=7323, confidence=0.6)
    claim = _item(type_="claim", statement="position bias is present", start=7082, end=7323, confidence=0.95)
    survivors, rejected = dedup_exact([limitation, claim])
# Claim 置信度更高 -> 替换 limitation，后者被拒绝。
    assert survivors == [claim]
    assert rejected == [limitation]


def test_claim_and_limitation_equal_confidence_keeps_first() -> None:
    limitation = _item(type_="limitation", statement="position bias", start=7082, end=7323, confidence=0.8)
    claim = _item(type_="claim", statement="position bias", start=7082, end=7323, confidence=0.8)
    survivors, rejected = dedup_exact([limitation, claim])
# 置信度相同 -> 首个（limitation）保留，claim 被拒绝。
    assert survivors == [limitation]
    assert rejected == [claim]


# ------------------------------------------------------------- 跨论文保护
def test_same_numeric_span_different_paper_not_merged() -> None:
    a = _item(statement="same statement", paper_id="p-1", start=100, end=200)
    b = _item(statement="same statement", paper_id="p-2", start=100, end=200)
    survivors, rejected = dedup_exact([a, b])
# 数值相同但论文不同 -> 不是重复项（范围键包含论文）。
    assert len(survivors) == 2
    assert rejected == []


# ------------------------------------------------------------- 无范围条目
def test_items_without_span_are_kept() -> None:
    a = _item()
    a["source_provenance"] = {"paper_id": "p-1", "batch_index": 0}  # no start/end
    survivors, rejected = dedup_exact([a])
    assert survivors == [a]
    assert rejected == []


def test_empty_input() -> None:
    assert dedup_exact([]) == ([], [])


# ------------------------------------------------------------- 幂等结构
def test_survivor_plus_rejected_equals_input_count() -> None:
    items = [
        _item(statement="same", start=1, end=5),
        _item(statement="same", start=1, end=5),
        _item(statement="other", start=10, end=20),
        _item(type_="limitation", statement="other", start=10, end=20, confidence=0.9),
        _item(statement="same", paper_id="p-9", start=1, end=5),  # different paper
    ]
    survivors, rejected = dedup_exact(items)
    assert len(survivors) + len(rejected) == len(items)


# ------------------------------------------------------------- P1：语义近重复

def _embed_from(vectors: dict[str, list[float]]):
    """批次 embedding 替身：未知文本获得不同的正交向量。"""
    return lambda texts: [vectors.get(t, [0.0, 1.0]) for t in texts]


def test_semantic_text_mirrors_signature() -> None:
    assert semantic_text({"statement": "GraphRAG is better"}) == "GraphRAG is better"
    assert semantic_text({"description": "KG is incomplete"}) == "KG is incomplete"
    assert semantic_text({"statement": "  padded  "}) == "padded"
    assert semantic_text({}) == ""
    assert semantic_text(None) == ""


def test_semantic_near_dup_same_paper_keeps_higher_confidence() -> None:
    a = _item(statement="GraphRAG 比普通 RAG 效果好", start=100, end=200, confidence=0.7)
    b = _item(statement="GraphRAG 优于传统 RAG 方法", start=300, end=400, confidence=0.9)
    embed = _embed_from({
        "GraphRAG 比普通 RAG 效果好": [1.0, 0.0],
        "GraphRAG 优于传统 RAG 方法": [0.999, 0.001],
    })
    survivors, rejected = dedup_semantic([a, b], embed_texts=embed)
    assert survivors == [b]  # higher confidence wins
    assert rejected == [a]


def test_semantic_below_threshold_both_survive() -> None:
    a = _item(statement="GraphRAG 比普通 RAG 效果好", start=100, end=200)
    b = _item(statement="完全不同的结论", start=300, end=400)
    embed = _embed_from({
        "GraphRAG 比普通 RAG 效果好": [1.0, 0.0],
        "完全不同的结论": [0.0, 1.0],
    })
    survivors, rejected = dedup_semantic([a, b], embed_texts=embed)
    assert len(survivors) == 2
    assert rejected == []


def test_semantic_cross_paper_never_merged() -> None:
    a = _item(statement="同一事实陈述", paper_id="p-1", start=100, end=200, confidence=0.7)
    b = _item(statement="同一事实陈述", paper_id="p-2", start=300, end=400, confidence=0.9)
    embed = _embed_from({"同一事实陈述": [1.0, 0.0]})
    survivors, rejected = dedup_semantic([a, b], embed_texts=embed)
    assert len(survivors) == 2
    assert rejected == []


def test_semantic_cross_type_never_merged() -> None:
    claim = _item(type_="claim", statement="同一事实陈述", start=100, end=200)
    limitation = _item(type_="limitation", statement="同一事实陈述", start=300, end=400)
    embed = _embed_from({"同一事实陈述": [1.0, 0.0]})
    survivors, rejected = dedup_semantic([claim, limitation], embed_texts=embed)
    assert len(survivors) == 2
    assert rejected == []


def test_semantic_empty_input() -> None:
    assert dedup_semantic([], embed_texts=lambda texts: []) == ([], [])


def test_semantic_empty_text_items_are_kept() -> None:
    a = _item(statement="", start=100, end=200)
    embed = _embed_from({})
    survivors, rejected = dedup_semantic([a], embed_texts=embed)
    assert survivors == [a]
    assert rejected == []


def test_semantic_embeds_all_substantive_texts_once() -> None:
    calls: list[list[str]] = []

    def recording(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[1.0, 0.0] for _ in texts]

    a = _item(statement="甲", start=1, end=2)
    b = _item(statement="乙", start=3, end=4)
    c = _item(statement="", start=5, end=6)  # empty -> excluded from embedding
    dedup_semantic([a, b, c], embed_texts=recording)
    assert len(calls) == 1
    assert calls[0] == ["甲", "乙"]


def test_semantic_counts_preserved() -> None:
    items = [
        _item(statement="GraphRAG 比普通 RAG 效果好", start=100, end=200, confidence=0.7),
        _item(statement="GraphRAG 优于传统 RAG 方法", start=300, end=400, confidence=0.9),
        _item(statement="完全不同的结论", start=500, end=600),
    ]
    embed = _embed_from({
        "GraphRAG 比普通 RAG 效果好": [1.0, 0.0],
        "GraphRAG 优于传统 RAG 方法": [0.999, 0.001],
        "完全不同的结论": [0.0, 1.0],
    })
    survivors, rejected = dedup_semantic(items, embed_texts=embed)
    assert len(survivors) + len(rejected) == len(items)
