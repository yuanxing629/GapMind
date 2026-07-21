"""PDF parsing: extract full text + section structure from a PDF.

This module is the core of Phase 2. It takes PDF bytes and produces:
  - Full text (cleaned, with page breaks marked)
  - Section structure (Abstract / Introduction / Method / ...)
  - Per-page text + char offsets (for evidence span grounding)

The output is a ParsedPdf dataclass that the chunker consumes next.

Design goals:
  - Never raise on malformed PDFs (return what we can, log warnings)
  - Preserve character offsets so EvidenceSpans can point back to source
  - Detect sections via PyMuPDF heading detection + heuristics
  - Clean common PDF extraction artifacts (broken hyphens, double columns)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF


# Canonical section names we try to recognize. Anything not matching
# falls into "Unknown".
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
    """A detected section heading inside the parsed text."""

    section: str  # canonical name, e.g. "Method"
    subsection: str | None  # raw heading text, e.g. "3.2 GNNExplainer Formulation"
    char_offset: int  # where this section starts in the full text
    page_number: int  # 1-based


@dataclass
class ParsedPdf:
    """Result of parsing a PDF into text + structure."""

    full_text: str
    page_count: int
    sections: list[SectionMarker] = field(default_factory=list)
    # Per-page char ranges in full_text, so we can map chars back to pages.
    page_char_ranges: list[tuple[int, int]] = field(default_factory=list)
    # Warnings (non-fatal issues encountered during parsing).
    warnings: list[str] = field(default_factory=list)


def parse_pdf(content: bytes) -> ParsedPdf:
    """Parse PDF bytes into ParsedPdf. Never raises on bad PDFs."""
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
            # "text" mode extracts reading order; "blocks" is better for
            # multi-column but slower. We use "text" and rely on cleaning
            # to fix most issues.
            raw = page.get_text("text")
            cleaned = _clean_page_text(raw)
            page_texts.append(cleaned)

        # Join pages with a form-feed separator so we can recover page
        # boundaries later.
        full_text_parts: list[str] = []
        page_char_ranges: list[tuple[int, int]] = []
        cursor = 0
        for pt in page_texts:
            start = cursor
            full_text_parts.append(pt)
            cursor += len(pt)
            # Add separator unless this is the last page and text is empty.
            page_char_ranges.append((start, cursor))
            if pt:
                full_text_parts.append("\f")  # form feed
                cursor += 1  # for the \f char

        full_text = "".join(full_text_parts)

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


# ----------------------------------------------------------------- cleaning
def _clean_page_text(raw: str) -> str:
    """Apply cleaning rules to a single page's text."""
    if not raw:
        return ""

    text = raw

    # 1. Fix broken hyphenation: "opti-\nmization" -> "optimization"
    # Only join when the part after newline is lowercase (typical for hyphenation).
    text = re.sub(r"(\w)-\n([a-z])", r"\1\2", text)

    # 2. Normalize whitespace: multiple spaces/tabs -> single space.
    text = re.sub(r"[ \t]+", " ", text)

    # 3. Strip trailing spaces on each line.
    text = "\n".join(line.rstrip() for line in text.split("\n"))

    # 4. Collapse 3+ newlines into 2 (paragraph break).
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 5. Remove reference citations like [12] or [12, 15] inline.
    # Keep this conservative - only match square-bracketed pure-number groups.
    text = re.sub(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]", "", text)

    # 6. Remove common page headers/footers.
    # We can't reliably detect these without per-page analysis, so we rely
    # on the caller to ignore short lines at page boundaries if needed.
    return text.strip() + "\n"


# ----------------------------------------------------- section detection
def _detect_sections(
    doc: fitz.Document,
    page_char_ranges: list[tuple[int, int]],
    page_texts: list[str],
) -> list[SectionMarker]:
    """Detect section headings using a mix of heuristics.

    Strategy:
      1. Use PyMuPDF's get_text("dict") to find lines with larger font size
         or bold style (typical heading indicators).
      2. Match heading text against KNOWN_SECTIONS and numbering patterns
         like "1. Introduction" or "3.2 Method".
      3. Fall back to regex on full text for numbered headings if dict-based
         detection finds nothing.
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
                # Heuristic: heading if line has above-average font size
                # OR all spans are bold.
                line_text = "".join(s.get("text", "") for s in spans).strip()
                if not line_text or len(line_text) > 100:
                    # Skip empty lines and paragraph-length "headings"
                    continue
                avg_size = sum(s.get("size", 0) for s in spans) / len(spans)
                is_bold = all("bold" in (s.get("font", "").lower()) for s in spans)
                # 11pt is a typical body threshold; headings usually 12+.
                is_large = avg_size >= 12.0
                if not (is_large or is_bold):
                    continue

                normalized, canonical = _classify_heading(line_text)
                if canonical is None:
                    continue
                # Avoid adding the same section twice (e.g. running headers).
                key = f"{canonical}:{page_index + 1}"
                if key in seen_sections:
                    continue
                seen_sections.add(key)

                # Compute the char offset of this heading inside full_text.
                # We search for the line_text in the page's text and offset
                # from page_start.
                offset_in_page = page_texts[page_index].find(normalized)
                if offset_in_page < 0:
                    # The cleaned page text may have removed characters; fall
                    # back to a rough position (start of page).
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

    # Sort by char offset so they're in document order.
    sections.sort(key=lambda s: s.char_offset)
    return sections


def _classify_heading(heading: str) -> tuple[str, str | None]:
    """Return (normalized_text, canonical_section_or_None).

    canonical_section is one of KNOWN_SECTIONS (canonicalized), or None if
    the heading doesn't look like a known section.
    """
    # Strip leading numbering like "1.", "1.2", "3.2.1"
    stripped = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", heading).strip()
    if not stripped:
        return heading, None
    lower = stripped.lower().rstrip(":.")
    # Try direct match
    if lower in KNOWN_SECTIONS:
        # Canonicalize to title case but use the canonical name form
        canonical = lower
        return stripped, _canonical_section_name(canonical)
    # Try fuzzy: "Experimental Results" contains "experiment" - check first word
    first_word = lower.split()[0] if lower.split() else ""
    if first_word in KNOWN_SECTIONS:
        return stripped, _canonical_section_name(first_word)
    return heading, None


def _canonical_section_name(lower: str) -> str:
    """Map a lower-case section keyword to its canonical display name."""
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
    """Return the 1-based page number containing this char offset.

    Returns 0 if the offset is out of range (shouldn't normally happen).
    """
    for i, (start, end) in enumerate(parsed.page_char_ranges):
        if start <= char_offset < end:
            return i + 1
    return 0
