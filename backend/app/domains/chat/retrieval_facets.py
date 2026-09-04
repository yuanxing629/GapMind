"""Workspace Chat 实验的确定性 query facet 规划。

本模块只规划候选 facet 查询。它刻意不调用 LLM、embedding provider、Milvus 或数据库。
生产 Chat 必须保留原始问题作为主查询；调用方只有在离线 A/B 评测证明启用合理后，
才可以使用返回的 facet。
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal


FacetName = Literal["formula", "method", "dataset", "comparison"]
MAX_RETRIEVAL_FACETS = 2


@dataclass(frozen=True)
class _FacetRule:
    name: FacetName
    triggers: tuple[str, ...]
    query_hint: str
    section_hints: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalFacet:
    """从用户原始问题派生的一条确定性 facet。"""

    name: FacetName
    query: str
    matched_triggers: tuple[str, ...]
    section_hints: tuple[str, ...]


_FACET_RULES: tuple[_FacetRule, ...] = (
    _FacetRule(
        name="formula",
        triggers=(
            "损失",
            "损失函数",
            "公式",
            "目标函数",
            "优化目标",
            "loss",
            "objective",
            "formula",
            "equation",
            "derivation",
        ),
        query_hint="formula equation loss objective formulation",
        section_hints=("Method", "Related Work"),
    ),
    _FacetRule(
        name="method",
        triggers=(
            "方法",
            "经典方法",
            "代表方法",
            "机制",
            "method",
            "approach",
            "mechanism",
            "architecture",
        ),
        query_hint="method approach mechanism architecture",
        section_hints=("Method", "Related Work"),
    ),
    _FacetRule(
        name="dataset",
        triggers=(
            "数据集",
            "基准数据集",
            "实验",
            "评价指标",
            "dataset",
            "benchmark",
            "experiment",
            "evaluation",
            "metrics",
        ),
        query_hint="dataset benchmark experiment evaluation metrics",
        section_hints=("Experiment", "Method"),
    ),
    _FacetRule(
        name="comparison",
        triggers=(
            "比较",
            "对比",
            "基线",
            "优于",
            "compare",
            "comparison",
            "baseline",
            "versus",
        ),
        query_hint="baseline comparison related work differences",
        section_hints=("Experiment", "Related Work"),
    ),
)


def _contains_trigger(question: str, trigger: str) -> bool:
    if trigger.isascii() and trigger.replace("_", "").replace("-", "").isalnum():
        return re.search(rf"(?<![a-z0-9]){re.escape(trigger)}(?![a-z0-9])", question) is not None
    return trigger in question


def plan_retrieval_facets(question: str) -> tuple[RetrievalFacet, ...]:
    """为 ``question`` 返回最多两个确定性 facet。

    规则顺序是有意设计且稳定的：公式问题优先于 method、dataset 和 comparison facet 处理。
    原始规范化问题会复制到每个规划查询中，避免未来调用方意外用仅关键词查询替换主查询。
    """

    normalized = " ".join(question.split()).strip().casefold()
    if not normalized:
        return ()

    facets: list[RetrievalFacet] = []
    for rule in _FACET_RULES:
        matched = tuple(trigger for trigger in rule.triggers if _contains_trigger(normalized, trigger))
        if not matched:
            continue
        facets.append(
            RetrievalFacet(
                name=rule.name,
                query=f"{normalized}\n检索重点：{rule.query_hint}",
                matched_triggers=matched,
                section_hints=rule.section_hints,
            )
        )
        if len(facets) >= MAX_RETRIEVAL_FACETS:
            break
    return tuple(facets)


__all__ = ["FacetName", "MAX_RETRIEVAL_FACETS", "RetrievalFacet", "plan_retrieval_facets"]
