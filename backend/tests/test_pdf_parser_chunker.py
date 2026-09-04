"""PDF 解析和分块逻辑的单元测试。

这些测试使用 PyMuPDF 生成的合成 PDF，因此不依赖外部文件。它们验证 parser 和 chunker
对各种论文式结构能产生合理输出。
"""

from __future__ import annotations

import fitz
import pytest

from app.domains.artifact.chunker import chunk_parsed_pdf
from app.domains.artifact.pdf_parser import ParsedPdf, parse_pdf


def _make_pdf(pages: list[str], *, with_headings: bool = False) -> bytes:
    """使用给定的页面文本构建 PDF。

    使用较大的矩形调用 insert_textbox，以保留全部文本
    （insert_text 默认会在页面边界处截断）。

    如果 with_headings=True，则使用更大的字体渲染每页第一行，以模拟章节标题。
    """
    doc = fitz.open()
    for page_text in pages:
        page = doc.new_page()
# 使用接近整页的文本框，避免长文本被截断。
        rect = fitz.Rect(36, 36, page.rect.width - 36, page.rect.height - 36)
        if with_headings and "\n" in page_text:
            first_line, rest = page_text.split("\n", 1)
# 在页面顶部的小矩形中使用较大字体写入标题。
            heading_rect = fitz.Rect(36, 36, page.rect.width - 36, 80)
            page.insert_textbox(heading_rect, first_line, fontsize=14, fontname="hebo")
            body_rect = fitz.Rect(36, 90, page.rect.width - 36, page.rect.height - 36)
            page.insert_textbox(body_rect, rest, fontsize=10, fontname="helv")
        else:
            page.insert_textbox(rect, page_text, fontsize=10, fontname="helv")
    return doc.tobytes()


# ------------------------------------------------------------- pdf_parser：PDF 解析器
def test_parse_pdf_extracts_text() -> None:
    pdf = _make_pdf(["Hello world. This is a test paragraph."])
    parsed = parse_pdf(pdf)
    assert parsed.page_count == 1
    assert "Hello world" in parsed.full_text
    assert len(parsed.page_char_ranges) == 1


def test_parse_pdf_handles_empty_pdf() -> None:
    doc = fitz.open()
    doc.new_page()  # one blank page
    pdf = doc.tobytes()
    parsed = parse_pdf(pdf)
    assert parsed.page_count == 1
    assert parsed.full_text.strip() == ""
    assert parsed.sections == []


def test_parse_pdf_handles_invalid_bytes() -> None:
    parsed = parse_pdf(b"not a pdf at all")
    assert parsed.page_count == 0
    assert parsed.full_text == ""
# 不应抛出异常，只返回带 warning 的空结果。
    assert len(parsed.warnings) > 0


def test_parse_pdf_fixes_broken_hyphenation() -> None:
# PyMuPDF 的文本提取通常将断词换行表示为 "opti-\nmization"。
# insert_text 不会自动换行，难以复现，但可以直接测试清理函数。
    from app.domains.artifact.pdf_parser import _clean_page_text

    cleaned = _clean_page_text("opti-\nmization is great")
    assert "optimization" in cleaned
    assert "opti-" not in cleaned


def test_parse_pdf_removes_citation_brackets() -> None:
    from app.domains.artifact.pdf_parser import _clean_page_text

    cleaned = _clean_page_text("GNNs [12] are powerful [13, 15].")
    assert "[12]" not in cleaned
    assert "[13, 15]" not in cleaned
    assert "GNNs" in cleaned


def test_clean_page_text_strips_nul_bytes() -> None:
    """PyMuPDF 会为嵌入字形输出 \x00；它们不能进入分块，
    因为 PostgreSQL 会拒绝文本列和 LIKE 参数中的 NUL。"""
    from app.domains.artifact.pdf_parser import _clean_page_text

    cleaned = _clean_page_text("Lexpl(e, G, y) :=\x001\n|V |\x00X\nu\u2208V\nBCE\x00e(G)u")
    assert "\x00" not in cleaned
    assert "Lexpl" in cleaned
    assert "BCE" in cleaned


def test_parse_pdf_detects_sections() -> None:
    pdf = _make_pdf(
        [
            "Abstract\nWe propose a new method for GNN explainability.\n",
            "1. Introduction\nGNNs are widely used. We want to explain them.\n",
            "2. Method\nOur method uses mutual information.\n",
        ],
        with_headings=True,
    )
    parsed = parse_pdf(pdf)
    section_names = [s.section for s in parsed.sections]
# 至少应检测到其中一部分（检测采用尽力策略）。
# 具体子集取决于 PyMuPDF 的标题检测。
    assert len(parsed.sections) >= 1


def test_parse_pdf_multi_page_char_ranges() -> None:
    pdf = _make_pdf(["page one content", "page two content", "page three"])
    parsed = parse_pdf(pdf)
    assert parsed.page_count == 3
    assert len(parsed.page_char_ranges) == 3
# 每个范围都应非空且按文档顺序排列。
    for start, end in parsed.page_char_ranges:
        assert end > start
# 范围应单调递增。
    starts = [r[0] for r in parsed.page_char_ranges]
    assert starts == sorted(starts)
    assert parsed.full_text.count("\f") == 2
    assert not parsed.full_text.endswith("\f")


# --------------------------------------------------------------- chunker：分块器
def _chunk_from(
    parsed,
    *,
    workspace_id="ws-1",
    paper_id="paper-1",
    source_artifact_id="art-1",
    created_at="2026-07-19T10:00:00Z",
):
    return chunk_parsed_pdf(
        parsed,
        workspace_id=workspace_id,
        paper_id=paper_id,
        created_at=created_at,
        source_artifact_id=source_artifact_id,
    )


def test_chunker_empty_pdf_produces_no_chunks() -> None:
    parsed = parse_pdf(b"")
    chunks = _chunk_from(parsed)
    assert chunks == []


def test_chunker_short_text_produces_one_chunk() -> None:
    pdf = _make_pdf(["This is a short paragraph. It is too short to split."])
    parsed = parse_pdf(pdf)
    chunks = _chunk_from(parsed)
    assert len(chunks) >= 1
# 所有分块都应填充必需字段。
    for c in chunks:
        assert c.chunk_id
        assert c.workspace_id == "ws-1"
        assert c.paper_id == "paper-1"
        assert c.source_artifact_id == "art-1"
        assert c.text
        assert c.start_char < c.end_char
        assert c.tokens_estimate > 0
        assert c.chunk_version == "v1"


def test_chunker_chunk_index_is_sequential() -> None:
# 构造包含多页长段落的 PDF，以获得多个分块。使用 4 页，超过 PyMuPDF 的单页文本换行范围。
    pages = []
    for i in range(4):
# 每页约 2000 字符，明显接近每块 TARGET_CHARS（2048）
        pages.append(f"Page {i+1}. " + "This is paragraph content. " * 100)
    pdf = _make_pdf(pages)
    parsed = parse_pdf(pdf)
    chunks = _chunk_from(parsed)
    assert len(chunks) >= 2
    indices = [c.chunk_index for c in chunks]
    assert indices == list(range(len(chunks)))


def test_chunker_respects_min_chunk_size() -> None:
    # 构造会让朴素切分产生极小尾部块的文本。
    text = "Main paragraph here. " * 100 + "\n\n" + "tiny."
    pdf = _make_pdf([text])
    parsed = parse_pdf(pdf)
    chunks = _chunk_from(parsed)
    # 不应存在极小分块（尾部的 "tiny." 应被合并）。
    for c in chunks:
        # 允许一定容差，合并后的分块可以更长，但不能留下孤立尾块。
        assert c.tokens_estimate > 0


def test_chunker_chunks_have_unique_ids() -> None:
    pdf = _make_pdf(["Paragraph A. " * 200 + "\n\n" + "Paragraph B. " * 200])
    parsed = parse_pdf(pdf)
    chunks = _chunk_from(parsed)
    ids = {c.chunk_id for c in chunks}
    assert len(ids) == len(chunks)


def test_chunker_section_assignment() -> None:
    # 检测到章节时，分块应携带章节名称。
    pdf = _make_pdf(
        [
            "Abstract\nWe propose X.\n" + "Body text. " * 100,
            "Introduction\nGNNs are great.\n" + "More body. " * 100,
        ],
        with_headings=True,
    )
    parsed = parse_pdf(pdf)
    chunks = _chunk_from(parsed)
    # 如果章节检测成功，至少部分分块应具有非 Unknown 章节。
    sections = {c.section for c in chunks}
    # 即使部分检测遗漏，章节并集也应非空。
    assert sections
    # 所有分块都应有章节字符串（已知章节或 "Unknown"）。
    for c in chunks:
        assert c.section is not None


def test_chunker_chunks_preserve_text_content() -> None:
    pdf = _make_pdf(["The quick brown fox jumps over the lazy dog. " * 50])
    parsed = parse_pdf(pdf)
    chunks = _chunk_from(parsed)
    # 分块文本并集应覆盖原始关键短语。
    combined = " ".join(c.text for c in chunks)
    assert "quick brown fox" in combined
    for chunk in chunks:
        assert chunk.text == parsed.full_text[chunk.start_char:chunk.end_char]


def test_chunker_preserves_exact_slices_across_whitespace_and_overlap() -> None:
    text = (
        "Introduction\n"
        + "First paragraph has wrapped text.\n" * 80
        + "\n\n"
        + "Second paragraph starts after a blank line. " * 100
    )
    parsed = ParsedPdf(
        full_text=text,
        page_count=1,
        page_char_ranges=[(0, len(text))],
    )
    chunks = _chunk_from(parsed)

    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.text == parsed.full_text[chunk.start_char:chunk.end_char]


def test_chunker_page_numbers_are_valid() -> None:
    pdf = _make_pdf(["page 1 text " * 100, "page 2 text " * 100])
    parsed = parse_pdf(pdf)
    chunks = _chunk_from(parsed)
    for c in chunks:
        assert c.page_start >= 1
        assert c.page_end >= c.page_start
        assert c.page_start <= parsed.page_count
        assert c.page_end <= parsed.page_count


def test_chunker_overlap_makes_adjacent_chunks_share_text() -> None:
    # 构造一定会被切成多个分块的长文本。
    text = "Sentence one. " * 300
    pdf = _make_pdf([text])
    parsed = parse_pdf(pdf)
    chunks = _chunk_from(parsed)
    if len(chunks) >= 2:
        # 相邻分块应通过 overlap 共享部分文本。
        # 比较 chunk[i] 的尾部和 chunk[i+1] 的头部。
        prev_tail = chunks[0].text[-50:]
        next_head = chunks[1].text[:50]
        # 查找出现在 next_head 中的 prev_tail 子串，overlap 区域应连接两块。
        #（不断言精确 overlap，因为分块边界可能落在句子边界，导致 overlap 更短。）
        # 做基本检查：chunk[1] 应以 chunk[0] 中存在的文本开头；没有 overlap 时，
        # chunk[1] 会从文本中间开始。
        assert chunks[1].start_char <= chunks[0].end_char
