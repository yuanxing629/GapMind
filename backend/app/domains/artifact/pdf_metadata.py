"""Best-effort PDF metadata extraction.

Uses PyMuPDF (fitz) to read the PDF's embedded metadata dict, which often
carries title / author / creationDate. Academic PDFs vary wildly in
metadata quality - some have well-formed author fields, many have nothing
useful. This module extracts what it can and leaves the rest for the caller
to fill or leave empty.

No LLM, no parsing of the body text - that's Phase 3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz  # PyMuPDF


@dataclass
class PdfMetadata:
    """Best-effort extracted metadata. Any field may be None/empty."""

    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    page_count: int = 0


def extract_metadata(content: bytes) -> PdfMetadata:
    """Extract metadata from PDF bytes.

    Opens the PDF in memory, reads its metadata dict, and tries to parse
    a year out of the creationDate. Returns PdfMetadata with whatever it
    could extract - never raises on malformed PDFs (returns empty fields).
    """
    result = PdfMetadata()
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        # Not a valid PDF or PyMuPDF can't open it - return empty.
        return result

    try:
        result.page_count = doc.page_count
        meta = doc.metadata or {}

        # Title: PDF metadata 'title' field, stripped.
        raw_title = (meta.get("title") or "").strip()
        if raw_title and _looks_like_title(raw_title):
            result.title = raw_title

        # Authors: PDF metadata 'author' field is often a single string
        # like "Alice; Bob" or "Alice, Bob" or "Alice and Bob". Split on
        # common separators.
        raw_author = (meta.get("author") or "").strip()
        if raw_author:
            authors = _split_authors(raw_author)
            if authors:
                result.authors = authors

        # Year: try to parse from creationDate ('D:YYYYMMDD...') first,
        # then fall back to modDate. If neither, leave None.
        result.year = _parse_year(meta.get("creationDate")) or _parse_year(
            meta.get("modDate")
        )
    finally:
        doc.close()

    return result


def _split_authors(raw: str) -> list[str]:
    """Split an author string on common separators."""
    # Split on ';', ',', ' and ', '&'. Keep parts that look like names.
    parts = re.split(r"[;,]|\band\b|&", raw)
    return [p.strip() for p in parts if _looks_like_author(p.strip())]


def _looks_like_title(s: str) -> bool:
    """Reject obviously-junk titles (PDF generators often leave 'Untitled' etc)."""
    if not s:
        return False
    lower = s.lower()
    junk = {"untitled", "microsoft word -", "draft", "pdf"}
    return not any(lower.startswith(j) for j in junk)


def _looks_like_author(s: str) -> bool:
    """Reject obviously-junk author strings."""
    if not s or len(s) > 200:
        return False
    # Must contain at least one letter and not be a URL / filename.
    if not re.search(r"[A-Za-z]", s):
        return False
    if "http" in s.lower() or ".pdf" in s.lower():
        return False
    return True


def _parse_year(raw: str | None) -> int | None:
    """Parse a 4-digit year out of a PDF date string like 'D:20240315...'."""
    if not raw:
        return None
    # PDF date format: D:YYYYMMDDHHmmSS+TZ. Just grab the first 4 digits.
    match = re.search(r"(?:D:)?(\d{4})", raw)
    if not match:
        return None
    year = int(match.group(1))
    # Sanity bound - reject obvious garbage.
    if 1900 <= year <= 2100:
        return year
    return None
