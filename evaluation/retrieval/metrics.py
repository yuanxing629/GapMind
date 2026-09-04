"""Retrieval Gate 指标——纯函数，无 I/O。

每个函数都接收普通数据（sets/lists/floats），因此 Gate 数学无需 DB、Milvus 或 LLM 即可
进行单元测试。

指标定义（docs/phase3_smoke_validation_and_next_plan.md §6 V2）：

* Recall@K——Top-K 结果中包含 gold 条目的比例
* MRR@K——第一个 gold 命中的平均倒数排名（不存在时为 0）
* nDCG@K——二元相关性的折损累计增益
* paper_diversity——Top-K 中不同论文数 ÷ min(K, 返回数量)
* workspace_leakage——返回条目中 workspace_id 不匹配查询工作区的比例（必须为 0）
"""

from __future__ import annotations

import math


def recall_at_k(gold: set[str], retrieved: list[str], k: int) -> float:
    """前 ``k`` 个检索 ID 中出现 gold 条目的比例。"""
    if not gold:
        return 0.0
    top_k = set(retrieved[:k])
    return len(gold & top_k) / len(gold)


def mrr_at_k(gold: set[str], retrieved: list[str], k: int) -> float:
    """Top-K 内第一个 gold 命中的倒数排名（没有时为 0）。"""
    for rank, item in enumerate(retrieved[:k], start=1):
        if item in gold:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(gold: set[str], retrieved: list[str], k: int) -> float:
    """采用二元相关性的 nDCG@K。"""
    if not gold:
        return 0.0
    dcg = 0.0
    for i, item in enumerate(retrieved[:k]):
        if item in gold:
            dcg += 1.0 / math.log2(i + 2)
    ideal_count = min(len(gold), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_count))
    return dcg / idcg if idcg > 0 else 0.0


def paper_diversity(retrieved: list[str], k: int) -> float:
    """Top-K 中不同论文数与理想最大值的比例。

    ``1.0`` 表示每个槽位来自不同论文；``0.25`` 表示四个命中都来自同一篇论文。
    当单篇论文的分块主导 Top-K 时，就会出现较低的多样性。
    """
    if not retrieved:
        return 0.0
    top_k = retrieved[:k]
    distinct = len(set(top_k))
    return distinct / min(k, len(top_k))


def workspace_leakage(workspace_ids: list[str], target_workspace_id: str) -> float:
    """属于其他 workspace 的返回条目比例。

    对工作区范围检索而言，该值必须严格为 0.0；任何正值都是安全/隔离缺陷，不能作为调参项。
    """
    if not workspace_ids:
        return 0.0
    leaked = sum(1 for wid in workspace_ids if wid != target_workspace_id)
    return leaked / len(workspace_ids)


def gate_report(
    *,
    recall: float,
    threshold: float,
    k: int = 10,
    mrr: float | None = None,
    ndcg: float | None = None,
    diversity: float | None = None,
    leakage: float | None = None,
) -> dict[str, object]:
    """构建报告 JSON 中每个 benchmark 的 Gate 判定块。"""
    recall_passed = recall >= threshold - 1e-9
    return {
        f"recall@{k}": round(recall, 4),
        "recall_threshold": threshold,
        "recall_passed": recall_passed,
        f"mrr@{k}": round(mrr, 4) if mrr is not None else None,
        f"ndcg@{k}": round(ndcg, 4) if ndcg is not None else None,
        "paper_diversity": round(diversity, 4) if diversity is not None else None,
        "workspace_leakage": round(leakage, 4) if leakage is not None else None,
        "passed": recall_passed and (leakage is None or leakage == 0.0),
    }


__all__ = [
    "recall_at_k",
    "mrr_at_k",
    "ndcg_at_k",
    "paper_diversity",
    "workspace_leakage",
    "gate_report",
]
