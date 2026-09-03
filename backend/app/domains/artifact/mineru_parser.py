"""MinerU local HTTP integration and output normalization.

GapMind keeps its own ``ParsedPdf`` contract so the downstream chunking,
knowledge extraction, and evidence code do not depend on a specific parser.
This module only handles the local MinerU API and converts its structured
output into that contract.
"""

from __future__ import annotations

import html
import io
import json
import re
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

import httpx

from app.core.config import settings
from app.domains.artifact.pdf_parser import (
    KNOWN_SECTIONS,
    ParsedPdf,
    SectionMarker,
    _canonical_section_name,
    _classify_heading,
    parse_pdf,
)


class MinerUError(RuntimeError):
    """Base error for a local MinerU request or response."""


class MinerUUnavailableError(MinerUError):
    """MinerU could not be reached."""


class MinerUParseError(MinerUError):
    """MinerU returned an unusable result."""


@dataclass(frozen=True)
class MinerUImage:
    """One image resource returned by MinerU's ZIP response."""

    relative_path: str
    content: bytes
    mime_type: str


@dataclass(frozen=True)
class MinerUParseResult:
    """Normalized MinerU result plus parser metadata for task observability."""

    parsed: ParsedPdf
    backend: str | None = None
    version: str | None = None
    images: tuple[MinerUImage, ...] = ()


def parse_with_mineru(
    content: bytes,
    *,
    api_url: str,
    timeout_seconds: float = 1800.0,
    client_factory: Callable[..., Any] = httpx.Client,
) -> MinerUParseResult:
    """Parse one PDF through a local ``mineru-api`` service.

    The API call is synchronous because this function already runs inside
    GapMind's background parse task. ``client_factory`` is intentionally
    injectable so tests can use ``httpx.MockTransport`` without a live service.
    """
    if not content:
        raise MinerUParseError("PDF content is empty")
    if not api_url.strip():
        raise MinerUUnavailableError("MinerU API URL is empty")

    timeout = max(float(timeout_seconds), 1.0)
    try:
        with client_factory(
            base_url=api_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=min(timeout, 30.0)),
        ) as client:
            response = client.post(
                "/file_parse",
                files={"files": ("document.pdf", content, "application/pdf")},
                data={
                    "backend": "pipeline",
                    "parse_method": "auto",
                    "return_md": "true",
                    "return_content_list": "true",
                    "return_middle_json": "true",
                    "image_analysis": "false",
                    "return_images": str(settings.parser_return_images).lower(),
                    "response_format_zip": "true",
                    "return_original_file": "false",
                },
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise MinerUUnavailableError(f"MinerU request failed: {exc}") from exc
    except httpx.HTTPError as exc:
        raise MinerUError(f"MinerU HTTP client failed: {exc}") from exc

    if response.status_code >= 400:
        detail = _response_error_detail(response)
        if response.status_code in {408, 429, 500, 502, 503, 504}:
            raise MinerUUnavailableError(
                f"MinerU returned HTTP {response.status_code}: {detail}"
            )
        raise MinerUParseError(
            f"MinerU returned HTTP {response.status_code}: {detail}"
        )

    try:
        output_files = _read_zip(response.content)
    except (zipfile.BadZipFile, OSError) as exc:
        detail = _response_error_detail(response)
        raise MinerUParseError(
            f"MinerU response is not a valid ZIP: {detail}"
        ) from exc

    result = _normalize_output_files(
        output_files,
        include_images=settings.parser_return_images,
    )
    return _prefer_pymupdf_text(content, result)


def normalize_mineru_output(
    files: Mapping[str, bytes],
    *,
    include_images: bool = True,
) -> MinerUParseResult:
    """Public alias used by tests and offline output validation scripts."""
    return _normalize_output_files(files, include_images=include_images)


def _prefer_pymupdf_text(
    content: bytes,
    mineru_result: MinerUParseResult,
) -> MinerUParseResult:
    """Use PyMuPDF text when it covers a digital PDF reliably.

    MinerU remains the source for layout-aware Markdown, equations, tables,
    and sections. Its current pipeline can nevertheless drop characters from
    ordinary PDF text (notably ``f`` in some embedded fonts). PyMuPDF gives us
    a clean text backstop for chunking and retrieval while preserving the
    structured MinerU Markdown for downstream extraction.
    """
    try:
        pymupdf_result = parse_pdf(content)
    except Exception:
        return mineru_result

    if not _has_sufficient_pymupdf_text(pymupdf_result, mineru_result.parsed):
        return mineru_result

    sections = _rebase_sections(
        mineru_result.parsed.sections,
        pymupdf_result,
    )
    warnings = list(mineru_result.parsed.warnings)
    warnings.append("mineru_plain_text_replaced_with_pymupdf")
    markdown = _repair_missing_f_words(
        mineru_result.parsed.to_markdown(),
        pymupdf_result.full_text,
    )
    parsed = ParsedPdf(
        full_text=pymupdf_result.full_text,
        page_count=pymupdf_result.page_count,
        sections=sections,
        page_char_ranges=pymupdf_result.page_char_ranges,
        warnings=warnings,
        _markdown=markdown,
    )
    return MinerUParseResult(
        parsed=parsed,
        backend=mineru_result.backend,
        version=mineru_result.version,
        images=mineru_result.images,
    )


def _has_sufficient_pymupdf_text(
    pymupdf: ParsedPdf,
    mineru: ParsedPdf,
) -> bool:
    if not pymupdf.full_text.strip() or pymupdf.page_count <= 0:
        return False

    pymupdf_pages = pymupdf.full_text.split("\f")
    nonempty_pages = sum(bool(page.strip()) for page in pymupdf_pages)
    coverage = nonempty_pages / max(pymupdf.page_count, 1)
    if coverage < 0.8:
        return False

    # Avoid replacing a substantial OCR result with a tiny hidden text layer.
    return len(pymupdf.full_text) >= max(20, int(len(mineru.full_text) * 0.3))


def _rebase_sections(
    sections: list[SectionMarker],
    parsed: ParsedPdf,
) -> list[SectionMarker]:
    """Map MinerU section markers onto the PyMuPDF text offsets."""
    if not sections:
        return []

    pages = parsed.full_text.split("\f")
    rebased: list[SectionMarker] = []
    seen: set[tuple[str, int, int]] = set()
    for marker in sections:
        page_index = min(max(marker.page_number - 1, 0), len(pages) - 1)
        page_text = pages[page_index]
        candidates = [candidate for candidate in (marker.subsection, marker.section) if candidate]
        offset_in_page = -1
        for candidate in candidates:
            offset_in_page = page_text.casefold().find(candidate.casefold())
            if offset_in_page >= 0:
                break
            prefix = _heading_prefix(candidate)
            if prefix:
                prefix_match = re.search(
                    rf"(?m)^\s*{re.escape(prefix)}\s*$",
                    page_text,
                )
                if prefix_match:
                    offset_in_page = prefix_match.start()
                    break
        if offset_in_page < 0:
            continue

        page_start = parsed.page_char_ranges[page_index][0]
        char_offset = page_start + offset_in_page
        key = (marker.section, page_index, char_offset)
        if key in seen:
            continue
        seen.add(key)
        rebased.append(
            SectionMarker(
                section=marker.section,
                subsection=marker.subsection,
                char_offset=char_offset,
                page_number=page_index + 1,
            )
        )
    return sorted(rebased, key=lambda marker: marker.char_offset)


def _heading_prefix(value: str) -> str | None:
    match = re.match(r"\s*((?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*))\b", value)
    return match.group(1) if match else None


def _repair_missing_f_words(markdown: str, reference_text: str) -> str:
    """Repair unique words missing one ``f`` using PyMuPDF as the oracle."""
    reference_words = set(re.findall(r"[A-Za-z]{4,}", reference_text.lower()))
    candidates: dict[str, set[str]] = {}
    for word in reference_words:
        for index, char in enumerate(word):
            if char == "f":
                candidates.setdefault(word[:index] + word[index + 1 :], set()).add(word)

    def replace(match: re.Match[str]) -> str:
        original = match.group(0)
        matches = candidates.get(original.lower(), set())
        if len(matches) != 1:
            return original
        replacement = next(iter(matches))
        if original.isupper():
            return replacement.upper()
        if original[:1].isupper():
            return replacement.capitalize()
        return replacement

    return re.sub(r"[A-Za-z]{4,}", replace, markdown)


def _read_zip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        result: dict[str, bytes] = {}
        for info in archive.infolist():
            if info.is_dir():
                continue
            # Do not let an output filename escape the logical result map.
            name = str(PurePosixPath(info.filename))
            result[name] = archive.read(info)
        if not result:
            raise zipfile.BadZipFile("MinerU ZIP is empty")
        return result


def _normalize_output_files(
    files: Mapping[str, bytes],
    *,
    include_images: bool = True,
) -> MinerUParseResult:
    markdown = _first_file(files, (".md",), exclude=("readme.md",))
    content_list_v2 = _first_file(files, ("content_list_v2.json",))
    content_list = _first_file(files, ("content_list.json",))
    middle = _first_file(files, ("middle.json",))

    raw_content: Any = None
    if content_list_v2 is not None:
        raw_content = _load_json(content_list_v2, "content_list_v2.json")
        version = "content_list_v2"
    elif content_list is not None:
        raw_content = _load_json(content_list, "content_list.json")
        version = "content_list"
    else:
        version = None

    backend, parser_version = _read_middle_metadata(middle)
    if raw_content is None:
        if markdown is None:
            raise MinerUParseError(
                "MinerU result has neither Markdown nor content_list JSON"
            )
        parsed = _markdown_only_result(markdown, include_images=include_images)
    else:
        parsed = _content_list_to_parsed(
            raw_content,
            markdown,
            version or "",
            include_images=include_images,
        )

    if parser_version is None:
        parser_version = version
    return MinerUParseResult(
        parsed=parsed,
        backend=backend,
        version=parser_version,
        images=_extract_image_resources(files) if include_images else (),
    )


_IMAGE_MIME_TYPES = {
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}


def _extract_image_resources(files: Mapping[str, bytes]) -> tuple[MinerUImage, ...]:
    """Extract safe image files while preserving MinerU's relative paths."""
    resources: list[MinerUImage] = []
    seen: set[str] = set()
    for name in sorted(files):
        path = PurePosixPath(name)
        if any(part in {"", ".", ".."} for part in path.parts):
            continue
        image_index = next(
            (index for index, part in enumerate(path.parts) if part.lower() == "images"),
            None,
        )
        mime_type = _IMAGE_MIME_TYPES.get(path.suffix.lower())
        if image_index is None or mime_type is None:
            continue
        relative_path = PurePosixPath(*path.parts[image_index:]).as_posix()
        if relative_path in seen:
            continue
        seen.add(relative_path)
        resources.append(
            MinerUImage(
                relative_path=relative_path,
                content=files[name],
                mime_type=mime_type,
            )
        )
    return tuple(resources)


def _first_file(
    files: Mapping[str, bytes],
    suffixes: tuple[str, ...],
    *,
    exclude: tuple[str, ...] = (),
) -> bytes | None:
    for name in sorted(files):
        lower = name.lower()
        if lower.endswith(suffixes) and not lower.endswith(exclude):
            return files[name]
    return None


def _load_json(content: bytes, filename: str) -> Any:
    try:
        return json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MinerUParseError(f"MinerU {filename} is invalid JSON") from exc


def _read_middle_metadata(content: bytes | None) -> tuple[str | None, str | None]:
    if content is None:
        return None, None
    try:
        data = _load_json(content, "middle.json")
    except MinerUParseError:
        return None, None
    if not isinstance(data, dict):
        return None, None
    backend = data.get("_backend")
    version = data.get("_version_name")
    return (
        str(backend) if backend else None,
        str(version) if version else None,
    )


def _content_list_to_parsed(
    raw: Any,
    markdown: bytes | None,
    version: str,
    *,
    include_images: bool = True,
) -> ParsedPdf:
    pages = _group_content_pages(raw, version)
    page_texts: list[str] = []
    heading_markers: list[tuple[int, int, str, int | None]] = []

    for page_index, blocks in enumerate(pages):
        block_texts: list[str] = []
        cursor = 0
        for block in blocks:
            if not isinstance(block, dict):
                continue
            text = _block_text(block)
            if not text:
                continue
            if block.get("type") in {"title", "text"} or block.get("text_level"):
                level = _heading_level(block)
                if level:
                    heading_markers.append((page_index, cursor, text, level))
            block_texts.append(text)
            cursor += len(text) + 2
        page_texts.append("\n\n".join(block_texts))

    full_text, page_ranges, page_starts = _join_pages(page_texts)
    sections: list[SectionMarker] = []
    for page_index, offset_in_page, heading, level in heading_markers:
        if page_index >= len(page_starts):
            continue
        section, subsection = _section_for_heading(heading, level)
        sections.append(
            SectionMarker(
                section=section,
                subsection=subsection,
                char_offset=page_starts[page_index] + offset_in_page,
                page_number=page_index + 1,
            )
        )

    warnings: list[str] = []
    if not any(page.strip() for page in page_texts):
        warnings.append("mineru_content_list_has_no_text")
    return ParsedPdf(
        full_text=full_text,
        page_count=len(page_texts),
        sections=sections,
        page_char_ranges=page_ranges,
        warnings=warnings,
        _markdown=_normalize_mineru_markdown(markdown, include_images=include_images)
        if markdown is not None
        else None,
    )


def _group_content_pages(raw: Any, version: str) -> list[list[dict[str, Any]]]:
    if not isinstance(raw, list):
        raise MinerUParseError("MinerU content list must be a JSON array")

    # content_list_v2 is page-grouped; the legacy format is a flat list with
    # page_idx on each block.
    if version == "content_list_v2" or all(isinstance(item, list) for item in raw):
        return [
            [item for item in page if isinstance(item, dict)]
            for page in raw
            if isinstance(page, list)
        ]

    grouped: dict[int, list[dict[str, Any]]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        page_index = item.get("page_idx", 0)
        try:
            page_index = max(int(page_index), 0)
        except (TypeError, ValueError):
            page_index = 0
        grouped.setdefault(page_index, []).append(item)
    if not grouped:
        return [[]]
    return [grouped.get(index, []) for index in range(max(grouped) + 1)]


_IGNORED_TYPES = {
    "header",
    "footer",
    "page_header",
    "page_footer",
    "page_number",
    "aside_text",
    "page_aside_text",
}


_MINERU_LAYOUT_TAG_RE = re.compile(
    r"</?\s*(?:sub|sup)(?:\s+[^>]*)?>",
    flags=re.IGNORECASE,
)


def _strip_mineru_layout_tags(value: str) -> str:
    """Remove baseline tags MinerU emits around ordinary text spans.

    MinerU's pipeline output may use HTML ``sub``/``sup`` tags for font
    baseline runs, including ordinary letters in body text. Formulas are
    represented separately as LaTeX blocks, so keeping these tags would make
    normal prose render as subscript/superscript and pollute retrieval text.
    The tag contents remain unchanged.
    """
    return _MINERU_LAYOUT_TAG_RE.sub("", value)


def _block_text(block: Mapping[str, Any]) -> str:
    block_type = str(block.get("type") or "")
    if block_type in _IGNORED_TYPES:
        return ""

    # Legacy content_list.json fields.
    legacy_keys = (
        "text",
        "code_body",
        "algorithm_body",
        "table_body",
        "image_caption",
        "table_caption",
        "table_footnote",
        "image_footnote",
        "text_content",
    )
    values = [block[key] for key in legacy_keys if key in block]
    if values:
        return _clean_text("\n".join(_value_text(value) for value in values))

    # content_list_v2 uses a structured content object.
    if "content" in block:
        return _clean_text(_value_text(block["content"]))
    return ""


def _value_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        if "<table" in value.lower() or "<td" in value.lower():
            return _html_to_text(value)
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    if isinstance(value, list):
        return "\n".join(_value_text(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") in {"image", "chart"} and "content" not in value:
            return ""
        # MinerU hyperlink spans expose both concatenated ``content`` and
        # style-preserving ``children``. The former is authoritative here;
        # reading both would duplicate text in parsed_text.
        if "content" in value:
            return _value_text(value["content"])
        preferred = [
            key
            for key in value
            if key.endswith(("_content", "_body", "_caption", "_footnote"))
            or key in {"content", "children", "list_items", "math_content"}
        ]
        return "\n".join(_value_text(value[key]) for key in preferred)
    return ""


def _html_to_text(value: str) -> str:
    value = re.sub(r"</(?:tr|p|div|br)>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    return html.unescape(value)


def _clean_text(value: str) -> str:
    value = _strip_mineru_layout_tags(value)
    value = value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _heading_level(block: Mapping[str, Any]) -> int | None:
    value = block.get("text_level")
    if value is None and isinstance(block.get("content"), dict):
        value = block["content"].get("level")
    try:
        level = int(value)
    except (TypeError, ValueError):
        return 1 if block.get("type") == "title" else None
    return max(level, 1) if level > 0 else None


def _section_for_heading(heading: str, level: int | None) -> tuple[str, str | None]:
    first_line = heading.splitlines()[0].strip()
    normalized, canonical = _classify_heading(first_line)
    if canonical:
        return canonical, first_line if normalized != canonical else None

    # Preserve meaningful MinerU titles even when they are outside our known
    # vocabulary; the chunker can still use them as section boundaries.
    stripped = re.sub(r"^\d+(?:\.\d+)*\.?\s*", "", first_line).strip()
    if not stripped:
        stripped = first_line
    if stripped.lower() in KNOWN_SECTIONS:
        canonical = _canonical_section_name(stripped.lower())
        return canonical, None
    return stripped or "Unknown", first_line if level and level > 1 else None


def _join_pages(page_texts: list[str]) -> tuple[str, list[tuple[int, int]], list[int]]:
    parts: list[str] = []
    ranges: list[tuple[int, int]] = []
    starts: list[int] = []
    cursor = 0
    for index, page_text in enumerate(page_texts):
        starts.append(cursor)
        parts.append(page_text)
        cursor += len(page_text)
        ranges.append((starts[-1], cursor))
        if index < len(page_texts) - 1:
            parts.append("\f")
            cursor += 1
    return "".join(parts), ranges, starts


def _markdown_only_result(
    markdown: bytes,
    *,
    include_images: bool = True,
) -> ParsedPdf:
    text = _normalize_mineru_markdown(markdown, include_images=include_images)
    plain = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    plain = _clean_text(plain)
    return ParsedPdf(
        full_text=plain,
        page_count=1,
        page_char_ranges=[(0, len(plain))],
        warnings=["mineru_content_list_missing_page_metadata"],
        _markdown=text,
    )


def _normalize_mineru_markdown(markdown: bytes, *, include_images: bool) -> str:
    text = _strip_mineru_layout_tags(
        markdown.decode("utf-8", errors="replace").replace("\x00", "")
    )
    if not include_images:
        text = re.sub(
            r"!\[[^\]]*\]\(\s*(?:<[^>]+>|[^)\s]+)\s*\)",
            "",
            text,
        )
    return text


def _response_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
        if isinstance(payload, dict):
            return str(payload.get("detail") or payload.get("message") or payload)
        return str(payload)
    except (ValueError, json.JSONDecodeError):
        return response.text[:500] or "empty response"
