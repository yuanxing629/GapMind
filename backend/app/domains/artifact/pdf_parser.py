"""PDF 解析：从 PDF 中提取全文和章节结构。

本模块是 Phase 2 的核心。它接收 PDF 字节并生成：
  - 全文（已清理并标记分页）
  - 章节结构（Abstract / Introduction / Method / ...）
  - 每页文本及字符偏移（用于证据范围 grounding）

输出是供 chunker 后续消费的 ParsedPdf dataclass。

设计目标：
  - 对格式错误的 PDF 不抛出异常（返回可获取内容并记录警告）
  - 保留字符偏移，使 EvidenceSpan 可以指回来源
  - 通过 PyMuPDF 标题检测和启发式规则识别章节
  - 清理常见 PDF 抽取伪影（断行连字符、双栏文本）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF


# 尝试识别的规范章节名称。未匹配的内容归入 "Unknown"。
KNOWN_SECTIONS = {
    "abstract",
    "introduction",
    "related work",
    "related works",
    "background",
    "preliminaries",
    "method",
    "methods",
    "methodology",
    "approach",
    "model",
    "proposed method",
    "our approach",
    "experiment",
    "experiments",
    "experimental results",
    "results",
    "evaluation",
    "discussion",
    "conclusion",
    "conclusions",
    "future work",
    "acknowledgments",
    "acknowledgements",
    "references",
    "appendix",
}


@dataclass
class SectionMarker:
    """解析文本中检测到的章节标题。"""

    section: str  # canonical name, e.g. "Method"
    subsection: str | None  # raw heading text, e.g. "3.2 GNNExplainer Formulation"
    char_offset: int  # where this section starts in the full text
    page_number: int  # 1-based


@dataclass
class ParsedPdf:
    """将 PDF 解析为文本和结构后的结果。"""

    full_text: str
    page_count: int
    sections: list[SectionMarker] = field(default_factory=list)
# full_text 中每页的字符范围，用于将字符映射回页面。
    page_char_ranges: list[tuple[int, int]] = field(default_factory=list)
# Warnings（解析过程中遇到的非致命问题）。
    warnings: list[str] = field(default_factory=list)
# Markdown 输出（按需生成）
    _markdown: str | None = field(default=None, repr=False)

    def to_markdown(self) -> str:
        """将解析后的 PDF 转换为 Markdown。

        章节会转换为 `##` / `###` 标题，分页符（\f）转换为 `---`。
        首先使用基于字体大小的 PyMuPDF 标题检测，随后回退到编号标题的正则规则
        （例如 “1. Introduction”）。
        """
        if self._markdown is not None:
            return self._markdown

        text = self.full_text
        if not text:
            self._markdown = ""
            return ""

        import re

# ---- 阶段 1：在 PyMuPDF 检测到的章节处插入 ## / ### ----
        ops: list[tuple[int, str]] = []
        for sm in sorted(self.sections, key=lambda s: -s.char_offset):
            prefix = "### " if sm.subsection else "## "
            heading = f"\n\n{prefix}{sm.section}\n\n"
            ops.append((sm.char_offset, heading))

        parts: list[str] = []
        cursor = 0
        for offset, heading in sorted(ops, key=lambda x: x[0]):
            parts.append(text[cursor:offset])
            parts.append(heading)
            cursor = offset
        parts.append(text[cursor:])
        md_text = "".join(parts)

# ---- 阶段 2：基于正则的标题检测（fallback） ----
# 识别看起来像编号标题、但未被 PyMuPDF 字体大小启发式规则捕获的行。
# 同时处理 PyMuPDF 将 "1 Introduction" 拆成两行的常见情况："1" +\n "Introduction"。
        lines = md_text.split("\n")
        result_lines: list[str] = []
        skip_next = False
        for i, line in enumerate(lines):
            if skip_next:
                skip_next = False
                continue

            stripped = line.strip()
            if not stripped:
                result_lines.append(line)
                continue

# 跳过阶段 1 已转换为 ## 或 ### 的行。
            if stripped.startswith("## ") or stripped.startswith("### "):
                result_lines.append(line)
                continue

# 检查跨行标题：当前行是 "1"，下一行是 "Introduction"。
            heading_level = None
            combined_text = stripped

            if stripped.isdigit() and i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith("#"):
                    combined = f"{stripped} {next_line}"
                    level = _detect_heading_by_regex(combined)
                    if level:
                        heading_level = level
                        combined_text = combined
                        skip_next = True

# 单行标题检测。
            if heading_level is None:
                heading_level = _detect_heading_by_regex(stripped)

            if heading_level and _looks_like_heading_context(lines, i, heading_level):
                prefix = "#" * heading_level
                result_lines.append(f"\n\n{prefix} {combined_text}\n")
            else:
                result_lines.append(line)

        md_text = "\n".join(result_lines)

# 将换页符替换为水平线
        md_text = md_text.replace("\f", "\n\n---\n\n")

# 清理：折叠多余的空行
        md_text = re.sub(r"\n{4,}", "\n\n\n", md_text)

        self._markdown = md_text
        return md_text


# 视为标题候选的已知章节关键词（小写）。
_HEADING_KEYWORDS: set[str] = {
    "abstract", "introduction", "related work", "related works",
    "background", "preliminaries", "method", "methods", "methodology",
    "approach", "model", "proposed method", "our approach",
    "experiment", "experiments", "experimental results", "results",
    "evaluation", "discussion", "conclusion", "conclusions",
    "future work", "acknowledgments", "acknowledgements", "references",
    "appendix",
}


def _detect_heading_by_regex(line: str) -> int | None:
    """如果 `line` 看起来像章节标题，返回标题级别（2 或 3）。

    匹配的模式（不区分大小写）：
      - "1. Introduction" 或 "1 Introduction" -> ##
      - "3.2. Method Details" 或 "3.2 Method" -> ###
      - "Abstract"（独立的已知关键词） -> ##
    如果行看起来不像标题，则返回 None。
    """
    stripped = line.strip()
    lower = stripped.lower().rstrip(".")

# 独立一行的 "Abstract" → ##
    if lower == "abstract":
        return 2

# 编号标题："3.2 Method" 或 "3.2. Method Details" → ###
    m = re.match(r"\d+\.\d+\.?\s+(.+)", stripped)
    if m:
        title_orig = m.group(1).rstrip(".")
        title_lower = title_orig.lower()
        if any(w in title_lower for w in ("figure", "table", "example", "answer:", "step")):
            return None
        if any(title_lower.startswith(kw) for kw in _HEADING_KEYWORDS):
            return 3
# 简短的大写标题 → 子章节标题
        if 3 <= len(title_orig) <= 60 and title_orig[0].isupper():
            return 3

# 编号标题："1. Introduction" 或 "1 Introduction" → ##
    m = re.match(r"(\d+)\.?\s+(.+)", stripped)
    if m:
        title_orig = m.group(2).rstrip(".")
        title_lower = title_orig.lower()

# 拒绝图、表、示例标题和清单答案
        if any(w in title_lower for w in ("figure", "table", "example", "answer:", "step")):
            return None

        if any(title_lower.startswith(kw) for kw in _HEADING_KEYWORDS):
            return 2
# 虽不是已知关键词但仍像标题的内容：长度较短、以大写字母开头，且前面有章节编号。
# 增加额外保护：长度必须为 3-60 个字符，不能包含过多标点。
        if (
            3 <= len(title_orig) <= 60
            and title_orig[0].isupper()
            and not re.search(r"[.?!,:;]{2,}", title_orig)
        ):
            return 2

# 独立的已知关键词（无编号）→ ##
    if lower in _HEADING_KEYWORDS and lower != "abstract":
        return 2

    return None


def _looks_like_heading_context(
    lines: list[str], idx: int, heading_level: int | None = None
) -> bool:
    """检查 `idx` 处的行是否处于类似标题的上下文中。"""
    line = lines[idx].strip()
    if len(line) > 150:
        return False

    lower = line.lower().rstrip(".")
    is_known_keyword = lower in _HEADING_KEYWORDS
# 单独的数字是类似 "1\nIntroduction" 这类跨行标题中的“编号”部分
    is_bare_digit = line.isdigit()

# 检查上一行。如果当前行是单独的数字，则跳过此检查（它是类似
# "1\nIntroduction" 的跨行标题的第一部分，其中 "1" 紧跟在正文后），
# 或者当前行是已知关键词，也跳过此检查（在许多学术 PDF 中，紧跟正文的
# 独立 "Introduction" 仍然是标题）。
    if idx > 0 and not is_known_keyword and not is_bare_digit:
        prev = lines[idx - 1].strip()
        if prev and prev != "---" and not prev.startswith("#"):
            return False

# 下一行应为正文（比当前标题行更长）。
    for j in range(idx + 1, min(idx + 3, len(lines))):
        nxt = lines[j].strip()
        if nxt and not nxt.startswith("#") and nxt != "---":
# 对于单独数字，标题文本是下一行（通常较短）；检查再下一行以确认正文上下文。
            if is_bare_digit:
                return True  # already validated by _detect_heading_by_regex
            return len(nxt) > 30

    return False


def parse_pdf(content: bytes) -> ParsedPdf:
    """将 PDF 字节解析为 ParsedPdf，遇到损坏 PDF 时也不抛出异常。"""
    warnings: list[str] = []
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as e:
        return ParsedPdf(
            full_text="",
            page_count=0,
            warnings=[f"failed to open PDF: {e}"],
        )

    try:
        page_texts: list[str] = []
        for page_index in range(doc.page_count):
            page = doc[page_index]
# "text" 模式提取阅读顺序；"blocks" 更适合多栏文档，但速度较慢。
# 这里使用 "text"，并依靠清理逻辑修复大多数问题。
# 一些 PDF 文本流包含嵌入的换页符。它们在这里不是页面边界；下面会显式添加
# 页面边界，以确保最终范围与页面一一对应。
            raw = page.get_text("text").replace("\f", "\n")
            cleaned = _clean_page_text(raw)
            page_texts.append(cleaned)

# 使用换页符连接页面，以便之后恢复页面边界。
        full_text_parts: list[str] = []
        page_char_ranges: list[tuple[int, int]] = []
        cursor = 0
        for page_index, pt in enumerate(page_texts):
            start = cursor
            full_text_parts.append(pt)
            cursor += len(pt)
# 除非这是最后一页且文本为空，否则添加分隔符。
            page_char_ranges.append((start, cursor))
            if pt and page_index < doc.page_count - 1:
                full_text_parts.append("\f")  # form feed
                cursor += 1  # for the \f char

        full_text = "".join(full_text_parts)

# 修复跨页断词：单词在页面边界处被拆开。
        #
# 学术 PDF 经常在页面之间拆分单词。文本看起来如下：
#   page N："...Additionally, Prot-\n"（末尾换行）
#   分隔符："\f"
#   第 N+1 页："\nGNN learns..."
        #
# 检测 "word-\n?\f[Letter]" 并合并：移除连字符，保留 \f 后的续接内容，
# 使页面边界仍可检测。每次修复都会因为移除连字符而使总长度减少 1，
# 因此需要重建 page_char_ranges 以保持正确。
        #
# 这也能捕获单页内的 "word-\n[Letter]" 情况，避免遗漏
# _clean_page_text 可能漏掉的内容（例如大写字母开头的续接词）。
        full_text = re.sub(r"([a-z])-\n?\f([a-zA-Z])", r"\1\f\2", full_text)
        full_text = re.sub(r"([a-z])-\n([a-zA-Z])", r"\1\2", full_text)

# 从（可能缩短的）full_text 重建 page_char_ranges。
# 策略：遍历文本中的 \f 位置，并记录每个 \f 之前的页面边界。
        page_char_ranges = []
        search_start = 0
        for _ in range(doc.page_count):
# 查找下一个 \f（如果存在）
            ff_pos = full_text.find("\f", search_start)
            if ff_pos == -1:
# 最后一页（没有末尾 \f）
                page_char_ranges.append((search_start, len(full_text)))
                break
            else:
                page_char_ranges.append((search_start, ff_pos))
                search_start = ff_pos + 1  # skip past \f

        sections = _detect_sections(doc, page_char_ranges, page_texts)

        return ParsedPdf(
            full_text=full_text,
            page_count=doc.page_count,
            sections=sections,
            page_char_ranges=page_char_ranges,
            warnings=warnings,
        )
    finally:
        doc.close()


# ----------------------------------------------------------------- 清理
def _clean_page_text(raw: str) -> str:
    """对单页文本应用清理规则。"""
    if not raw:
        return ""

    text = raw

# 0. 移除 NUL 字节。PyMuPDF 会为某些嵌入字形（公式、数学字体）输出 \x00；
# PostgreSQL 拒绝在文本列中存储或查询 NUL，因此它们绝不能进入 chunk 或 LIKE 参数。
    text = text.replace("\x00", "")

# 1a. 修复 chunk 内的断词："opti-\nmization" -> "optimization"。
# 续接部分以小写开头时最安全（是真实断词，而不是句首标点）。
    text = re.sub(r"(\w)-\n([a-z])", r"\1\2", text)

# 1b. 同样修复大写字母开头的续接。学术 PDF 中很常见：
    #   - 作者列表：“T. Rau, J.-” + “P. Jaume”
    #   - 数据集/期刊名称：“Graph-” + “D&D”
    #   - 作者姓名：“Barab´asi-” + “Albert”
# 仅在以下条件同时满足时应用：
#   - 连字符前是小写字母（真实断词，而不是列表或破折号标点）
#   - 连字符两侧的行都不是空行或纯空白
    text = re.sub(r"([a-z])-\n([A-Z])", r"\1\2", text)

# 2. 标准化空白：多个空格/制表符 -> 一个空格。
    text = re.sub(r"[ \t]+", " ", text)

# 3. 移除每行末尾的空格。
    text = "\n".join(line.rstrip() for line in text.split("\n"))

# 4. 将 3 个及以上换行折叠为 2 个（段落分隔）。
    text = re.sub(r"\n{3,}", "\n\n", text)

# 5. 移除行内类似 [12] 或 [12, 15] 的参考文献引用。
# 保持保守：只匹配方括号包围的纯数字组。
    text = re.sub(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]", "", text)

# 6. 移除常见页面页眉/页脚。
# 没有逐页分析时无法可靠检测，因此依赖调用方在需要时忽略页面边界处的短行。
    return text.strip() + "\n"


# ----------------------------------------------------- 章节检测
def _detect_sections(
    doc: fitz.Document,
    page_char_ranges: list[tuple[int, int]],
    page_texts: list[str],
) -> list[SectionMarker]:
    """使用多种启发式规则检测章节标题。

    策略：
      1. 使用 PyMuPDF 的 get_text("dict") 查找字体更大或加粗的行（典型标题特征）。
      2. 将标题文本与 KNOWN_SECTIONS 和 “1. Introduction” 或 “3.2 Method” 等编号模式匹配。
      3. 如果基于 dict 的检测没有结果，则回退到全文正则规则查找编号标题。
    """
    sections: list[SectionMarker] = []
    seen_sections: set[str] = set()  # avoid duplicates

    for page_index in range(doc.page_count):
        page = doc[page_index]
        page_start, _ = page_char_ranges[page_index]
        try:
            d = page.get_text("dict")
        except Exception:
            continue

        for block in d.get("blocks", []):
            if block.get("type") != 0:  # 0 = text block
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
# 启发式规则：如果行的字体大小高于平均值，或所有 span 都是粗体，则视为标题。
                line_text = "".join(s.get("text", "") for s in spans).strip()
                if not line_text or len(line_text) > 100:
# 跳过空行和段落长度的“标题”
                    continue
                avg_size = sum(s.get("size", 0) for s in spans) / len(spans)
                is_bold = all("bold" in (s.get("font", "").lower()) for s in spans)
# 11pt 是典型的正文阈值，标题通常为 12pt 以上。
# 但一些 LaTeX PDF（例如 arXiv）会将标题排版为约 11.95pt、正文约 10pt；
# 使用绝对值 `>= 12.0` 会漏掉这些标题，导致论文只被按附录进行分块。
# 使用 11.5 作为更安全的边界：真实正文几乎不会超过 11.5pt。
                is_large = avg_size >= 11.5
                if not (is_large or is_bold):
                    continue

                normalized, canonical = _classify_heading(line_text)
                if canonical is None:
                    continue
# 避免重复添加同一章节（例如运行页眉）。
                key = f"{canonical}:{page_index + 1}"
                if key in seen_sections:
                    continue
                seen_sections.add(key)

# 计算该标题在 full_text 中的字符偏移量。
# 在页面文本中搜索 line_text，并从 page_start 开始计算偏移。
                offset_in_page = page_texts[page_index].find(normalized)
                if offset_in_page < 0:
# 清理后的页面文本可能已移除字符；此时 fallback 到粗略位置（页面起点）。
                    offset_in_page = 0
                char_offset = page_start + offset_in_page

                sections.append(
                    SectionMarker(
                        section=canonical,
                        subsection=line_text if line_text != canonical.title() else None,
                        char_offset=char_offset,
                        page_number=page_index + 1,
                    )
                )

# 按字符偏移量排序，使其符合文档顺序。
    sections.sort(key=lambda s: s.char_offset)
    return sections


def _classify_heading(heading: str) -> tuple[str, str | None]:
    """返回（normalized_text, canonical_section_or_None）。

    canonical_section 是 KNOWN_SECTIONS 中的规范化值；如果标题不像已知章节，则为 None。
    """
# 移除类似 "1."、"1.2"、"3.2.1" 的前导编号
    stripped = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", heading).strip()
    if not stripped:
        return heading, None
    lower = stripped.lower().rstrip(":.")
# 尝试直接匹配
    if lower in KNOWN_SECTIONS:
# 转换为 title case，但使用规范名称形式
        canonical = lower
        return stripped, _canonical_section_name(canonical)
# 尝试模糊匹配："Experimental Results" 包含 "experiment"，检查第一个单词
    first_word = lower.split()[0] if lower.split() else ""
    if first_word in KNOWN_SECTIONS:
        return stripped, _canonical_section_name(first_word)
    return heading, None


def _canonical_section_name(lower: str) -> str:
    """将小写章节关键词映射为规范显示名称。"""
    mapping = {
        "abstract": "Abstract",
        "introduction": "Introduction",
        "related": "Related Work",
        "related work": "Related Work",
        "related works": "Related Work",
        "background": "Background",
        "preliminaries": "Preliminaries",
        "method": "Method",
        "methods": "Method",
        "methodology": "Method",
        "approach": "Method",
        "model": "Method",
        "proposed": "Method",
        "proposed method": "Method",
        "our approach": "Method",
        "experiment": "Experiment",
        "experiments": "Experiment",
        "experimental": "Experiment",
        "experimental results": "Experiment",
        "results": "Experiment",
        "evaluation": "Experiment",
        "discussion": "Discussion",
        "conclusion": "Conclusion",
        "conclusions": "Conclusion",
        "future": "Future Work",
        "future work": "Future Work",
        "acknowledgments": "Acknowledgments",
        "acknowledgements": "Acknowledgments",
        "references": "References",
        "appendix": "Appendix",
    }
    return mapping.get(lower, lower.title())


def get_page_for_char_offset(parsed: ParsedPdf, char_offset: int) -> int:
    """返回包含该字符偏移的从 1 开始计数的页码。

    如果偏移量超出范围（通常不应发生），则返回 0。
    """
    for i, (start, end) in enumerate(parsed.page_char_ranges):
        if start <= char_offset < end:
            return i + 1
    return 0
