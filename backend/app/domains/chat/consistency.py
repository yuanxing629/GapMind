"""校验以 [E1] / [E2] 形式引用证据的 LLM 输出的一致性。

Workspace RAG prompt 要求模型使用映射到证据排名的 [En] 标记支撑关键主张。
本模块校验这些标记是否真实——失效标记（引用不存在的证据）意味着模型幻觉式生成了引用，
而 grounded 回答完全没有标记则表示“key claims unsupported”。本模块是纯函数，不执行 I/O。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CITATION_PATTERN = re.compile(r"\[E(\d+)\]")
SOURCE_MARKER_PATTERN = re.compile(r"\[(P|D|C)(\d+)\]")


@dataclass
class CitationCheckResult:
    """校验一段文本中的 [En] 标记与证据索引后的结果。"""
    referenced: list[int] = field(default_factory=list)
    valid: list[int] = field(default_factory=list)
    broken: list[int] = field(default_factory=list)
    grounded_without_citations: bool = False
    ok: bool = True


@dataclass
class SourceMarkerCheckResult:
    """校验一条回答中非论文来源标记后的结果。"""

    referenced: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)
    ok: bool = True


def check_citation_markers(text: str, valid_indices: set[int]) -> CitationCheckResult:
    """返回被引用、有效和失效的 [En] 标记。

    ``valid_indices`` 是实际存在的证据排名集合。不在集合中的标记均视为幻觉式引用。
    """
    referenced = sorted({int(m) for m in CITATION_PATTERN.findall(text or "")})
    valid = [i for i in referenced if i in valid_indices]
    broken = [i for i in referenced if i not in valid_indices]
    return CitationCheckResult(referenced=referenced, valid=valid, broken=broken, ok=not broken)


def message_citation_check(content: str, citation_ranks: list[int], *, grounded: bool) -> CitationCheckResult:
    """校验 chat assistant 消息中的 [En] 标记与其 citations。

    当消息使用工作区证据生成时 ``grounded`` 为 True（grounding_status == "grounded"）；
    grounded 回答没有标记时，标记为“key claims unsupported”。
    """
    result = check_citation_markers(content, set(r for r in citation_ranks if r is not None))
    result.grounded_without_citations = grounded and not result.referenced
    return result


def source_marker_check(content: str, valid_markers: set[str]) -> SourceMarkerCheckResult:
    """校验 [P1]/[D1]/[C1] 标记与持久化 source passport。"""

    referenced = sorted(
        {f"[{kind}{index}]" for kind, index in SOURCE_MARKER_PATTERN.findall(content or "")}
    )
    broken = [marker for marker in referenced if marker not in valid_markers]
    return SourceMarkerCheckResult(referenced=referenced, broken=broken, ok=not broken)
