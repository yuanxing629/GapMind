"""文本 chunker：将 ParsedPdf 切分为适合检索的分块。

策略（依据 data_contracts.md 契约 #1）：
  1. 先按章节边界切分（来自 SectionMarker）
  2. 在章节内按段落切分（双换行）
  3. 段落过长时按句子切分
  4. 将过短分块与邻近分块合并（至少 100 tokens）
  5. 硬上限为 800 tokens（必要时在句中强制切分）
  6. 相邻分块重叠约 50 tokens

目标：每个分块 512 ± 50 tokens。

Token 估算：为提高速度，使用简单启发式（1 token ≈ 4 个字符）。
契约允许 `tokens_estimate` 为近似值；实际 embedding 模型（BGE-m3）会正确分词。
如需精确数量，可将 `_estimate_tokens` 替换为真实 tokenizer。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.domains.artifact.pdf_parser import ParsedPdf, get_page_for_char_offset

# 可调参数
TARGET_TOKENS = 512
MIN_TOKENS = 100
MAX_TOKENS = 800
OVERLAP_TOKENS = 50
CHARS_PER_TOKEN = 4  # cheap heuristic

TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN  # ~2048
MIN_CHARS = MIN_TOKENS * CHARS_PER_TOKEN  # ~400
MAX_CHARS = MAX_TOKENS * CHARS_PER_TOKEN  # ~3200
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN  # ~200


@dataclass
class Chunk:
    """一条可导出为 JSONL 的分块（契约 #1）。"""

    chunk_id: str
    workspace_id: str
    paper_id: str
    source_artifact_id: str
    chunk_index: int
    section: str | None
    subsection: str | None
    text: str
    start_char: int
    end_char: int
    page_start: int
    page_end: int
    tokens_estimate: int
    chunk_version: str
    created_at: str  # ISO 8601

    @property
    def artifact_id(self) -> str:
        """source_artifact_id 的弃用 v0.1 别名。"""
        return self.source_artifact_id


def chunk_parsed_pdf(
    parsed: ParsedPdf,
    *,
    workspace_id: str,
    paper_id: str,
    created_at: str,
    source_artifact_id: str | None = None,
    artifact_id: str | None = None,
    chunk_version: str = "v1",
) -> list[Chunk]:
    """依据契约 #1 将 ParsedPdf 切分为 Chunks。"""
    if not parsed.full_text.strip():
        return []
    source_id = source_artifact_id or artifact_id
    if not source_id:
        raise ValueError("source_artifact_id is required")

# 构建章节边界：由 (start_char, section_name, subsection) 组成的列表。
# 每个章节的“end”就是下一个章节的起点。
    section_starts: list[tuple[int, str, str | None]] = []
    for sm in parsed.sections:
        section_starts.append((sm.char_offset, sm.section, sm.subsection))
# 在文本末尾增加 section="Unknown" 的最终边界，以便捕获最后一个章节的内容。
    section_starts.append((len(parsed.full_text), "Unknown", None))

# 如果没有检测到章节，则将整个文档视为一个 “Unknown” 章节。
    if len(section_starts) == 1:
        section_starts.insert(0, (0, "Unknown", None))

    chunks: list[Chunk] = []
    chunk_index = 0

    for i in range(len(section_starts) - 1):
        sec_start, sec_name, sec_sub = section_starts[i]
        sec_end = section_starts[i + 1][0]
# 跳过空章节。
        section_text = parsed.full_text[sec_start:sec_end]
        if not section_text.strip():
            continue

# 步骤 1：将章节拆分为段落。
        paragraphs = _split_paragraphs(section_text)

# 步骤 2：将较小的段落合并，直到达到 TARGET_CHARS。
        merged_paras = _merge_paragraphs(paragraphs, section_text)

# 步骤 3：如果合并后的段落过长，则继续拆分。
        for para_text, para_offset_in_section in merged_paras:
            pieces = _split_long_text(para_text)

# 步骤 4：在相邻片段之间增加 overlap。
            pieces_with_overlap = _add_overlap(pieces, para_text)

            for piece_text, piece_start_in_para in pieces_with_overlap:
                if not piece_text.strip():
                    continue
# 计算绝对字符偏移量
                abs_start = sec_start + para_offset_in_section + piece_start_in_para
                abs_end = min(
                    abs_start + len(piece_text),
                    len(parsed.full_text),
                )
# 绝不持久化重构出来的空白。通过移动偏移量完成 trim，
# 然后截取不可变 parsed_text 中的精确片段。
                source_piece = parsed.full_text[abs_start:abs_end]
                left_trim = len(source_piece) - len(source_piece.lstrip())
                right_trim = len(source_piece) - len(source_piece.rstrip())
                abs_start += left_trim
                abs_end -= right_trim
                if abs_end <= abs_start:
                    continue
                source_piece = parsed.full_text[abs_start:abs_end]

                page_start = get_page_for_char_offset(parsed, abs_start)
                page_end = get_page_for_char_offset(parsed, abs_end - 1) if abs_end > abs_start else page_start
                if page_end == 0:
                    page_end = page_start
                if page_start == 0:
                    page_start = page_end

                tokens_est = _estimate_tokens(source_piece)

                chunks.append(
                    Chunk(
                        chunk_id=str(uuid.uuid4()),
                        workspace_id=workspace_id,
                        paper_id=paper_id,
                        source_artifact_id=source_id,
                        chunk_index=chunk_index,
                        section=sec_name,
                        subsection=sec_sub,
                        text=source_piece,
                        start_char=abs_start,
                        end_char=abs_end,
                        page_start=page_start,
                        page_end=page_end,
                        tokens_estimate=tokens_est,
                        chunk_version=chunk_version,
                        created_at=created_at,
                    )
                )
                chunk_index += 1

# 最后一轮：如果末尾的微小 chunk 与前一个 chunk 属于同一章节，则合并二者，
# 避免产生只有约 50 个 token 的孤立片段。
    chunks = _merge_tiny_tail_chunks(chunks, parsed.full_text)

# 合并后重新编排 chunk_index。
    for i, c in enumerate(chunks):
        c.chunk_index = i

    return chunks


# ----------------------------------------------------------------- 辅助函数
def _estimate_tokens(text: str) -> int:
    """低成本 token 估算：英文论文文本约每 4 个字符对应 1 个 token。"""
# BGE-m3 / cl100k_base 对英文平均约为 4 chars/token。
# 向上取整，采用更保守的估计。
    return max(1, len(text) // CHARS_PER_TOKEN)


def _split_paragraphs(text: str) -> list[tuple[str, int]]:
    """按双换行将文本切分为段落，返回（text、offset）对。"""
    result: list[tuple[str, int]] = []
    cursor = 0
    for raw_para in re.split(r"\n\s*\n", text):
        if not raw_para:
# 计入已经消耗的分隔符
            continue
# 查找该段落在原始文本中的实际位置。
        offset = text.find(raw_para, cursor)
        if offset < 0:
            offset = cursor
# 包含 re.split 可能剥离的前导空白
        result.append((raw_para, offset))
        cursor = offset + len(raw_para)
    return result


def _merge_paragraphs(
    paras: list[tuple[str, int]],
    source_text: str,
) -> list[tuple[str, int]]:
    """合并连续段落，直到达到 TARGET_CHARS。"""
    if not paras:
        return []
    merged: list[tuple[str, int]] = []
    buf_text = ""
    buf_offset = 0
    buf_end = 0

    for text, offset in paras:
        if not buf_text:
            buf_text = text
            buf_offset = offset
            buf_end = offset + len(text)
        elif offset + len(text) - buf_offset <= TARGET_CHARS:
            buf_end = offset + len(text)
            buf_text = source_text[buf_offset:buf_end]
        else:
            merged.append((source_text[buf_offset:buf_end], buf_offset))
            buf_text = text
            buf_offset = offset
            buf_end = offset + len(text)
    if buf_text:
        merged.append((source_text[buf_offset:buf_end], buf_offset))
    return merged


def _split_long_text(text: str) -> list[tuple[str, int]]:
    """文本过长时按句子切分，返回（text、offset）对。

    偏移量相对于 `text` 的起始位置。
    """
    if len(text) <= MAX_CHARS:
        return [(text, 0)]

    pieces: list[tuple[str, int]] = []
    sentence_boundaries = [
        match.end()
        for match in re.finditer(r"(?<=[.!?])\s+(?=[A-Z])", text)
    ]
    start = 0
    while start < len(text):
        remaining = len(text) - start
        if remaining <= MAX_CHARS:
            end = len(text)
        else:
            minimum = start + MIN_CHARS
            target = start + TARGET_CHARS
            maximum = min(start + MAX_CHARS, len(text))
            candidates = [
                boundary
                for boundary in sentence_boundaries
                if minimum <= boundary <= maximum
            ]
            if candidates:
                end = min(candidates, key=lambda value: abs(value - target))
            else:
                whitespace = max(
                    text.rfind(" ", minimum, maximum),
                    text.rfind("\n", minimum, maximum),
                )
                end = whitespace + 1 if whitespace >= minimum else maximum
        pieces.append((text[start:end], start))
        start = end
    return pieces


def _add_overlap(
    pieces: list[tuple[str, int]],
    source_text: str,
) -> list[tuple[str, int]]:
    """为每个片段（首个除外）添加重叠：将前一片段的尾部前置。"""
    if len(pieces) <= 1:
        return pieces
    result: list[tuple[str, int]] = [pieces[0]]
    for i in range(1, len(pieces)):
        cur_text, cur_offset = pieces[i]
        adjusted_offset = max(0, cur_offset - OVERLAP_CHARS)
        current_end = cur_offset + len(cur_text)
        result.append(
            (
                source_text[adjusted_offset:current_end],
                adjusted_offset,
            )
        )
    return result


def _merge_tiny_tail_chunks(
    chunks: list[Chunk],
    source_text: str,
) -> list[Chunk]:
    """如果章节的最后一个分块小于 MIN_TOKENS，则合并到前一个分块。"""
    if len(chunks) < 2:
        return chunks
    result = [chunks[0]]
    for c in chunks[1:]:
        prev = result[-1]
        if (
            c.tokens_estimate < MIN_TOKENS
            and c.section == prev.section
            and prev.tokens_estimate + c.tokens_estimate < MAX_TOKENS
        ):
# 使用精确的 source slice 合并，并保留两个 chunk 之间的原始空白。
            prev.text = source_text[prev.start_char:c.end_char]
            prev.end_char = c.end_char
            prev.page_end = c.page_end
            prev.tokens_estimate = _estimate_tokens(prev.text)
        else:
            result.append(c)
    return result
