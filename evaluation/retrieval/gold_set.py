"""Retrieval Gate benchmark 的 Gold-set schemas。

GoldSet 是冻结的人工标注清单，用于描述给定案例中“良好”检索的表现。
它会提供给 ``run_eval.py``，由其执行三个检索函数并计算 Stage-2 Gate 指标。

论文引用保持*可移植*：``paper_ref`` 是运行时解析为本地 UUID 的字符串
（标题匹配 -> external_paper_id 匹配 -> 直接 UUID）。这样 Gold 文件便于人工阅读
（标注者按标题引用论文），同时清单不会硬编码本地数据库主键。

路径依据：``docs/phase3_smoke_validation_and_next_plan.md`` §6 V2 要求
“使用 ``evaluation/retrieval/`` 统一运行并保存结果”，因此本包位于该规范路径。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Freeze(BaseModel):
    """每份报告中记录的版本锁，用于保证可复现性。

    字段对应 ``app.core.config.Settings``。如果任一值发生变化，变化前生成的报告
    就不能与变化后生成的报告直接比较。
    """

    chunk_version: str = "v1"
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
# 每份冻结清单都会记录具体模型。新清单保持 provider-neutral，
# 不将某次部署的模型名称写死。
    judge_model: str = ""


class SemanticSearchQuery(BaseModel):
    """自由文本 query，其 gold 答案是*目标论文*。

    Gate 指标：``target_paper_ref`` 是否出现在 Top-K 结果中？
    """

    query_id: str
    query: str = Field(min_length=3, max_length=500)
    target_paper_ref: str
    note: str | None = None


class SimilarWorkQuery(BaseModel):
    """给定源论文后，哪些*其他*论文在论文级别相关？

    Gate 指标：Top-K 中出现了多少个 ``relevant_paper_refs``
    （每篇论文去重为一个命中）？检索调用必须排除源论文本身。
    """

    query_id: str
    source_paper_ref: str
    relevant_paper_refs: list[str] = Field(min_length=1, max_length=20)
    note: str | None = None


class CounterRole(BaseModel):
    """claim 的 counter-evidence 命中预期角色。"""

    paper_ref: str
    role: Literal["contradicts", "qualifies", "supports", "overlaps", "unknown"]


class CounterEvidenceQuery(BaseModel):
    """一个 claim，其 gold 答案是一组（paper、role）对。

    粗粒度门禁指标：基于 ``gold_roles`` 计算论文级 Recall@10（忽略角色）。
    角色正确召回率单独作为诊断指标报告——Stage-2 阈值针对论文召回率，而不是角色准确率。

    ``claim_type`` 用于 V4 专项校验中的主张分组
    （``A_fact`` | ``B_qualified`` | ``C_first_novel``）。
    """

    query_id: str
    claim_text: str = Field(min_length=3, max_length=2000)
    source_paper_ref: str
    gold_roles: list[CounterRole] = Field(min_length=1, max_length=20)
    claim_type: str | None = Field(default=None)
    note: str | None = None


class GoldSet(BaseModel):
    """一个案例的完整标注 benchmark。"""

    schema_version: str = "1.0.0"
    case_id: str
    corpus_version: str
    freeze: Freeze = Field(default_factory=Freeze)
    workspace_hint: str | None = Field(
        default=None,
        description="Local UUID of the workspace. CLI --workspace-id overrides it.",
    )
    semantic_search: list[SemanticSearchQuery] = Field(default_factory=list)
    similar_work: list[SimilarWorkQuery] = Field(default_factory=list)
    counter_evidence: list[CounterEvidenceQuery] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_at_least_one_benchmark(self) -> "GoldSet":
        if not (self.semantic_search or self.similar_work or self.counter_evidence):
            raise ValueError("GoldSet must declare at least one benchmark")
        return self


__all__ = [
    "Freeze",
    "GoldSet",
    "SemanticSearchQuery",
    "SimilarWorkQuery",
    "CounterEvidenceQuery",
    "CounterRole",
]
