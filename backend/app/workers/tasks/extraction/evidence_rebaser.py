"""将 LLM 报告的 evidence span 与实际 parsed_markdown 对齐。

LLM 会输出 ``start_char``/``end_char`` 和 ``evidence_text`` 摘录。可能发生三种偏移：

  1. 偏移错误（混淆了相对于 batch 和相对于文档的偏移）；
  2. 文本经过空白规范化，直接切片比较失败；
  3. LLM 丢弃了末尾换行，因此切片相差 1 个字符。

本模块处理已知的各种偏移，并返回精确的 ``(start, end, text)`` 三元组以回链主文档；
如果证据无法恢复，则抛出 ``ValueError``。
"""

from __future__ import annotations

import re
from typing import Any


def all_occurrences(text: str, needle: str) -> list[int]:
    """返回 ``needle`` 在 ``text`` 中出现的所有索引。"""
    matches: list[int] = []
    cursor = 0
    while True:
        index = text.find(needle, cursor)
        if index < 0:
            return matches
        matches.append(index)
        cursor = index + 1


def nearest_match(matches: list[int], expected: int) -> int:
    return min(matches, key=lambda match: abs(match - expected))


def whitespace_equivalent_matches(text: str, evidence_text: str) -> list[tuple[int, int]]:
    """忽略空白，在 ``text`` 中查找 ``evidence_text`` 的出现位置。

    当 LLM 合并空格而文档保留空格时作为回退。返回 ``text`` 中的
    ``[(start, end), ...]`` 偏移。
    """
    tokens = re.split(r"\s+", evidence_text.strip())
    if not tokens or any(not token for token in tokens):
        return []
    pattern = r"\s+".join(re.escape(token) for token in tokens)
    return [(match.start(), match.end()) for match in re.finditer(pattern, text)]


def resolve_evidence_span(
    *,
    paper_text: str,
    batch_text: str,
    batch_start: int,
    reported_start: int,
    reported_end: int,
    evidence_text: str,
) -> tuple[int, int, str]:
    """返回基于 ``paper_text`` 解析得到的 ``(start, end, text)``。

    ``text`` 是 ``paper_text`` 在 ``start`` 和 ``end`` 之间的精确切片，*不是* LLM 规范化
    后的 ``evidence_text``，因此持久化的行与用户看到的 markdown 一致。
    """
    relative_end = reported_start + len(evidence_text)
    if reported_start >= 0 and batch_text[reported_start:relative_end] == evidence_text:
        start = batch_start + reported_start
        return start, start + len(evidence_text), evidence_text

    if (
        reported_start >= 0
        and paper_text[reported_start : reported_start + len(evidence_text)]
        == evidence_text
    ):
        return reported_start, reported_start + len(evidence_text), evidence_text

    batch_matches = all_occurrences(batch_text, evidence_text)
    if batch_matches:
        relative_start = nearest_match(batch_matches, reported_start)
        start = batch_start + relative_start
        return start, start + len(evidence_text), evidence_text

    document_matches = all_occurrences(paper_text, evidence_text)
    if document_matches:
        expected_positions = [batch_start + reported_start, reported_start]
        start = min(
            document_matches,
            key=lambda match: min(abs(match - expected) for expected in expected_positions),
        )
        return start, start + len(evidence_text), evidence_text

    batch_whitespace_matches = whitespace_equivalent_matches(batch_text, evidence_text)
    if batch_whitespace_matches:
        relative_start, relative_end = min(
            batch_whitespace_matches,
            key=lambda match: abs(match[0] - reported_start),
        )
        start = batch_start + relative_start
        end = batch_start + relative_end
        return start, end, paper_text[start:end]

    document_whitespace_matches = whitespace_equivalent_matches(paper_text, evidence_text)
    if document_whitespace_matches:
        expected_positions = [batch_start + reported_start, reported_start]
        start, end = min(
            document_whitespace_matches,
            key=lambda match: min(abs(match[0] - expected) for expected in expected_positions),
        )
        return start, end, paper_text[start:end]

    raise ValueError(
        "evidence_text has no exact or whitespace-equivalent parsed_markdown span"
    )


__all__ = [
    "all_occurrences",
    "nearest_match",
    "resolve_evidence_span",
    "whitespace_equivalent_matches",
]
