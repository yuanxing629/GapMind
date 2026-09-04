"""知识抽取的 claim/limitation 去重（P0 精确 + P1 语义）。

LLM 抽取过程中，同一证据范围可能出现多次：

* 同一事实跨批次被抽取两次，产生两个具有相同（type、span、content）的条目；
* 同一范围同时被分类为 claim 和 limitation（RG-1 中的 LLM-as-a-Judge 案例），
  从而产生共享范围但类型不同的两个项。

``dedup_exact`` 会在写入任何内容前合并这两类情况，并返回被拒绝的项，
以便调用方将其记录为 ``ExtractionRejection`` 行（可审计，绝不硬删除）。

RG-1 还发现了*近似*重复项：同一事实在两个*不同*范围中被抽取
（例如 “KG coverage 65.8%” 两次都被抽取为 limitation）。这类情况需要使用向量相似度，
对应 ``dedup_semantic``（P1，通过 ``retrieval_dedup_semantic`` 功能开关控制）。该策略刻意保持保守：

  * 只比较同一篇论文（``source_provenance.paper_id``）中的项——即使相似度很高，也绝不合并跨论文项；
  * 只合并相同 ``type`` 的项（claim 绝不会并入 limitation）；
  * 相似度必须达到 ``SEMANTIC_DUP_THRESHOLD``（0.90）——宁可保留两个近似重复项，也不静默合并两个不同事实。

Method/task/dataset 项已经携带共享的 ``canonical_entity_id``
（见 ``KnowledgeService.get_or_create_canonical_entity``）；这些项的精确范围重复项仍应丢弃，
避免图谱中出现两个几乎相同的提及。

本模块刻意不产生副作用（仅处理 ``list[dict]`` 的纯函数），因此无需数据库或 LLM 就能进行单元测试，
与 ``extraction/batching.py`` / ``llm_caller.py`` 的模式一致。
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Callable

# 参与跨类型同范围冲突处理的类型。Method 与 claim 共享范围时仍是不同事实；
# 只有 claim 与 limitation 被视为同一事实的不同分类。
_DEDUP_CROSS_TYPES = frozenset({"claim", "limitation"})

# P1 语义阈值。0.9 是有意设置的保守值：宁可保留两个近重复项，也不静默合并
# 两个不同事实。
SEMANTIC_DUP_THRESHOLD = 0.90


def content_signature(content: dict[str, Any] | None) -> str:
    """实质内容文本的稳定签名。

    使用 claim statement / limitation description（承载语义的部分），而不是整个
    content dict；后者可能包含尚未规范化的额外字段（scope、conditions、severity）。
    """
    content = content or {}
    text = str(content.get("statement") or content.get("description") or "")
    return hashlib.sha256(text.strip().casefold().encode("utf-8")).hexdigest()[:16]


def _span(item: dict[str, Any]) -> tuple[Any, ...] | None:
    """规范化范围键：``(paper_id, start_char, end_char)``。

    论文身份是键的一部分，因此来自*不同*论文、但恰好共享同一 ``(start_char, end_char)``
    的两个条目不会被当作重复项。条目没有 ``paper_id`` 时使用 ``artifact_id`` 作为回退。
    """
    sp = item.get("source_provenance") or {}
    start, end = sp.get("start_char"), sp.get("end_char")
    if start is None or end is None:
        return None
    paper_key = sp.get("paper_id") or sp.get("artifact_id") or ""
    return (str(paper_key), int(start), int(end))


def dedup_exact(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """合并精确重复项和同范围跨类型冲突。

    返回 ``(survivors, rejected)``。

    规则：
      1. 相同 ``(type, span, content_signature)`` → 保留第一项，拒绝其余项（适用于所有
         type，包括 method）；
      2. 相同 span 下出现 ``claim`` 与 ``limitation`` → 保留 confidence 更高的项，拒绝另一项。

    无法解析 span 的条目始终保留（没有可用于建立键的签名）。
    """
    survivors: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    by_exact: dict[tuple[Any, ...], dict[str, Any]] = {}
    by_span: dict[tuple[Any, ...], dict[str, Any]] = {}

    for item in items:
        item_type = item.get("type")
        span = _span(item)
        sig = content_signature(item.get("content"))

        if span is None:
# 没有范围，无法据此建立键，保留该项。
            survivors.append(item)
            continue

        exact_key = (item_type, span, sig)

# 规则 1：精确重复（类型、范围和实质内容都相同）。
        if exact_key in by_exact:
            rejected.append(item)
            continue

# 规则 2：相同范围下跨类型的 claim/limitation 冲突。
        if item_type in _DEDUP_CROSS_TYPES and span in by_span:
            prev = by_span[span]
            if prev.get("type") in _DEDUP_CROSS_TYPES and prev.get("type") != item_type:
# 同一事实的两种备选分类，保留置信度更高者。
                if item.get("confidence", 0.0) > prev.get("confidence", 0.0):
# 用当前 item 替换前一个条目：移除前一个条目的精确键和范围索引。
                    prev_sig = content_signature(prev.get("content"))
                    by_exact.pop((prev.get("type"), span, prev_sig), None)
                    survivors.remove(prev)
                    by_span[span] = item
                    rejected.append(prev)
                else:
                    rejected.append(item)
                    continue

        by_exact[exact_key] = item
        by_span.setdefault(span, item)
        survivors.append(item)

    return survivors, rejected


# ------------------------------------------------------------- P1：语义近重复

def semantic_text(content: dict[str, Any] | None) -> str:
    """用于语义比较的实质文本。

    与 ``content_signature`` 一致：claim statement / limitation description 承载语义；
    scope/conditions/severity 等额外字段不会用于相似度计算。
    """
    content = content or {}
    return str(content.get("statement") or content.get("description") or "").strip()


def _paper_key(item: dict[str, Any]) -> str:
    """用于同论文保护的论文身份（与 ``_span`` 对应）。"""
    sp = item.get("source_provenance") or {}
    return str(sp.get("paper_id") or sp.get("artifact_id") or "")


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def dedup_semantic(
    items: list[dict[str, Any]],
    *,
    embed_texts: Callable[[list[str]], list[list[float]]],
    threshold: float = SEMANTIC_DUP_THRESHOLD,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """P1：通过 embedding cosine 合并近重复的 claim/limitation。

    注入 ``embed_texts(list[str]) -> list[vector]``，便于测试替换（真实调用方通过
    ``EmbeddingGateway.embed_texts`` 批处理）。返回 ``(survivors, rejected)``。

    强制保护：
* 只比较同一论文内相同 ``type`` 的条目——limitation 永远不会折叠进 claim，听起来相似
  的两篇论文也永远不会合并；
* 相似度必须达到 ``>= threshold``（默认 0.90）。

    匹配时保留 confidence 更高的项，另一项作为 rejected 返回，以便调用方记录
    ``ExtractionRejection``（不会硬删除任何数据）。没有实质文本（或没有 type）的条目
    不参与去重，始终保留。
    """
    if not items:
        return [], []

    texts = [semantic_text(item.get("content")) for item in items]
    indexable = [i for i, text in enumerate(texts) if text]

    vectors: list[list[float] | None] = [None] * len(items)
    if indexable:
        embedded = embed_texts([texts[i] for i in indexable])
        for j, idx in enumerate(indexable):
            vectors[idx] = embedded[j]

# 按（论文、类型）对可比较条目分组。
    groups: dict[tuple[str, str], list[tuple[int, list[float], dict[str, Any]]]] = {}
    for i, item in enumerate(items):
        item_type = item.get("type")
        if not item_type or not texts[i]:
            continue
        groups.setdefault(
            (_paper_key(item), str(item_type)), []
        ).append((i, vectors[i] or [], item))

    survivors: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    deduped_indices: set[int] = set()

    for (_paper, item_type), entries in groups.items():
        kept: list[tuple[int, list[float], dict[str, Any]]] = []
        for idx, emb, item in entries:
            deduped_indices.add(idx)
            dup = False
            for prev_idx, prev_emb, prev in kept:
                if _cosine(emb, prev_emb) >= threshold:
# 同论文、同类型的语义近重复，保留置信度更高者。
                    if item.get("confidence", 0.0) > prev.get("confidence", 0.0):
                        kept.remove((prev_idx, prev_emb, prev))
                        kept.append((idx, emb, item))
                        rejected.append(prev)
                    else:
                        rejected.append(item)
                    dup = True
                    break
            if not dup:
                kept.append((idx, emb, item))
        survivors.extend(item for _, _, item in kept)

# 不属于任何（论文、类型）分组的条目永不去重。
    survivors.extend(
        item for i, item in enumerate(items) if i not in deduped_indices
    )

    return survivors, rejected


__all__ = ["content_signature", "dedup_exact", "dedup_semantic", "semantic_text"]
