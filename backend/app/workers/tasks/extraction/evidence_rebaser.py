"""将 LLM 报告的 evidence span 与实际 parsed_markdown 对齐。

LLM 会输出 ``start_char``/``end_char`` 和 ``evidence_text`` 摘录。可能发生三种偏移：

  1. 偏移错误（混淆了相对于 batch 和相对于文档的偏移）；
  2. 文本经过空白规范化，直接切片比较失败；
  3. LLM 丢弃了末尾换行，因此切片相差 1 个字符。

本模块处理已知的各种偏移，并返回精确的 ``(start, end, text)`` 三元组以回链主文档；
如果证据无法恢复，则抛出 ``ValueError``。
"""

from __future__ import annotations

import unicodedata


_PUNCTUATION_TRANSLATION = str.maketrans(
    {
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "‘": "'",
        "’": "'",
        "‚": "'",
        "“": '"',
        "”": '"',
        "„": '"',
    }
)
_IGNORABLE_CHARACTERS = {"\u00ad", "\u200b", "\u200c", "\u200d", "\ufeff"}


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


def _normalized_characters(
    text: str, *, join_line_hyphens: bool = False, remove_line_hyphens: bool = False
) -> tuple[str, list[tuple[int, int]]]:
    """构造匹配用文本，并保留归一化字符到原文的偏移映射。"""
    characters: list[tuple[str, int, int]] = []
    for index, character in enumerate(text):
        if character in _IGNORABLE_CHARACTERS:
            continue
        normalized = unicodedata.normalize("NFKC", character).translate(
            _PUNCTUATION_TRANSLATION
        )
        for normalized_character in normalized:
            if normalized_character not in _IGNORABLE_CHARACTERS:
                characters.append((normalized_character, index, index + 1))

    normalized_chars: list[str] = []
    source_spans: list[tuple[int, int]] = []
    index = 0
    while index < len(characters):
        character, start, end = characters[index]
        if character == "-" and (join_line_hyphens or remove_line_hyphens):
            cursor = index + 1
            has_line_break = False
            while cursor < len(characters) and characters[cursor][0].isspace():
                has_line_break = has_line_break or characters[cursor][0] in "\r\n"
                cursor += 1
            if has_line_break and cursor < len(characters):
                if join_line_hyphens:
                    normalized_chars.append("-")
                    source_spans.append((start, end))
                index = cursor
                continue

        if character.isspace():
            cursor = index + 1
            whitespace_end = end
            while cursor < len(characters) and characters[cursor][0].isspace():
                whitespace_end = characters[cursor][2]
                cursor += 1
            normalized_chars.append(" ")
            source_spans.append((start, whitespace_end))
            index = cursor
            continue

        normalized_chars.append(character)
        source_spans.append((start, end))
        index += 1

    while normalized_chars and normalized_chars[0] == " ":
        normalized_chars.pop(0)
        source_spans.pop(0)
    while normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        source_spans.pop()
    return "".join(normalized_chars), source_spans


def normalized_equivalent_matches(
    text: str, evidence_text: str
) -> list[tuple[int, int]]:
    """按安全的 Unicode/排版归一化查找证据，并返回原文偏移。

    允许的差异仅包括 Unicode 连字、不可见格式字符、常见排版标点、空白，
    以及 PDF 换行产生的断词连字符；不执行语义相似或模糊搜索。
    """
    evidence, _ = _normalized_characters(evidence_text)
    if not evidence:
        return []

    matches: set[tuple[int, int]] = set()
    for join_line_hyphens, remove_line_hyphens in (
        (False, False),
        (True, False),
        (False, True),
    ):
        normalized_text, source_spans = _normalized_characters(
            text,
            join_line_hyphens=join_line_hyphens,
            remove_line_hyphens=remove_line_hyphens,
        )
        cursor = 0
        while True:
            start = normalized_text.find(evidence, cursor)
            if start < 0:
                break
            end = start + len(evidence)
            if end <= len(source_spans):
                matches.add((source_spans[start][0], source_spans[end - 1][1]))
            cursor = start + 1
    return sorted(matches)


def whitespace_equivalent_matches(text: str, evidence_text: str) -> list[tuple[int, int]]:
    """兼容旧调用方的名称，按归一化等价查找证据。

    返回 ``text`` 中的 ``[(start, end), ...]`` 原始字符偏移。
    """
    return normalized_equivalent_matches(text, evidence_text)


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

    batch_normalized_matches = normalized_equivalent_matches(batch_text, evidence_text)
    if batch_normalized_matches:
        relative_start, relative_end = min(
            batch_normalized_matches,
            key=lambda match: abs(match[0] - reported_start),
        )
        start = batch_start + relative_start
        end = batch_start + relative_end
        return start, end, paper_text[start:end]

    document_normalized_matches = normalized_equivalent_matches(paper_text, evidence_text)
    if document_normalized_matches:
        expected_positions = [batch_start + reported_start, reported_start]
        start, end = min(
            document_normalized_matches,
            key=lambda match: min(abs(match[0] - expected) for expected in expected_positions),
        )
        return start, end, paper_text[start:end]

    raise ValueError(
        "evidence_text has no exact or normalized-equivalent parsed_markdown span"
    )


__all__ = [
    "all_occurrences",
    "nearest_match",
    "normalized_equivalent_matches",
    "resolve_evidence_span",
    "whitespace_equivalent_matches",
]
