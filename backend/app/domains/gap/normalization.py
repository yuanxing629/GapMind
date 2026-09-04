"""跨论文 gap-board 轴的可审计 domain 分类体系。

微调后的 extractor 有意输出属于单篇论文的描述性标签。棋盘需要更粗粒度且稳定的词汇，
否则每篇论文都会产生自己的行和列。只有存在透明的 domain 标记时，这些规则才会折叠
标签；未匹配的标签保持独立。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxonomyMatch:
    label: str
    rule_id: str
    confidence: float


def _has(text: str, *markers: str) -> bool:
    return any(marker.lower() in text for marker in markers)


def canonical_axis_label(axis_type: str, label: str, description: str = "") -> TaxonomyMatch | None:
    """返回保守的 GNN-explanation family，或 ``None``。

    规则顺序是有意设计的：先匹配具体的反事实机制，避免其描述先被更宽泛的因果 family
    捕获。
    """

    text = f"{label} {description}".lower()
    if axis_type == "method":
        if _has(text, "双视角", "事实推理和反事实", "必要性概率", "充分性概率"):
            return TaxonomyMatch("事实—反事实联合解释", "tax:m:joint_reasoning", 0.96)
        if _has(text, "信息瓶颈", "置信度矩阵", "置信度损失"):
            return TaxonomyMatch("信息瓶颈与置信度解释", "tax:m:confidence_ib", 0.96)
        if _has(text, "反事实") and _has(
            text, "扩散", "生成式", "生成模型", "变分自编码", "vae", "潜在空间"
        ):
            return TaxonomyMatch("生成式反事实解释", "tax:m:generative_cf", 0.95)
        if _has(text, "反事实") and _has(
            text, "蒙特卡洛", "树搜索", "启发式", "边删除", "矩阵稀疏", "最小扰动"
        ):
            return TaxonomyMatch("搜索与扰动式反事实解释", "tax:m:search_cf", 0.95)
        if _has(
            text,
            "因果结构",
            "因果推断",
            "因果效应",
            "统计检验",
            "条件平均处理效应",
            "cate",
        ):
            return TaxonomyMatch("因果推断与统计检验解释", "tax:m:causal", 0.95)
        return None

    if axis_type != "problem":
        return None
    if _has(text, "信息泄露", "隐式线索"):
        return TaxonomyMatch("掩码信息泄露", "tax:p:mask_leakage", 0.97)
    if _has(text, "可操作"):
        return TaxonomyMatch("解释可操作性不足", "tax:p:actionability", 0.97)
    if _has(text, "反事实") and _has(text, "时序", "动态图", "tgnn"):
        return TaxonomyMatch("时序图反事实解释不足", "tax:p:temporal_cf", 0.96)
    if _has(text, "反事实") and _has(text, "因果", "因果关系", "因果知识"):
        return TaxonomyMatch("反事实解释因果一致性不足", "tax:p:causal_cf", 0.95)
    if _has(text, "反事实") and _has(
        text, "缺乏", "不足", "研究较少", "探索", "生成", "最小输入扰动"
    ):
        return TaxonomyMatch("反事实解释覆盖不足", "tax:p:cf_coverage", 0.94)
    if _has(text, "评估", "基线", "真实标签", "必要性", "充分性"):
        return TaxonomyMatch("解释评估机制不足", "tax:p:evaluation", 0.94)
    if _has(text, "置信度", "分布外", "可靠性量化", "鲁棒性"):
        return TaxonomyMatch("解释可靠性与置信度不足", "tax:p:reliability", 0.94)
    if _has(
        text,
        "虚假相关",
        "统计因果",
        "个体级干预",
        "不忠实",
        "因果解释器",
        "因果性检验",
    ):
        return TaxonomyMatch("解释因果可靠性不足", "tax:p:causal_reliability", 0.94)
    return None
