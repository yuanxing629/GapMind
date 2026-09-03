"""Parser selection for the paper ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.domains.artifact.mineru_parser import (
    MinerUImage,
    MinerUParseResult,
    parse_with_mineru,
)
from app.domains.artifact.pdf_parser import ParsedPdf, parse_pdf


@dataclass(frozen=True)
class DocumentParseResult:
    """Parser-neutral result consumed by the existing parse task."""

    parsed: ParsedPdf
    provider: str
    backend: str | None = None
    version: str | None = None
    images: tuple[MinerUImage, ...] = ()


def parse_document(content: bytes) -> DocumentParseResult:
    """Parse with the configured provider, optionally falling back locally."""
    if settings.parser_provider != "mineru_local":
        return DocumentParseResult(parsed=parse_pdf(content), provider="pymupdf")

    try:
        result: MinerUParseResult = parse_with_mineru(
            content,
            api_url=settings.mineru_api_url,
            timeout_seconds=settings.mineru_timeout_seconds,
        )
        return DocumentParseResult(
            parsed=result.parsed,
            provider="mineru_local",
            backend=result.backend,
            version=result.version,
            images=result.images,
        )
    except Exception as exc:
        if not settings.parser_fallback_enabled:
            raise
        parsed = parse_pdf(content)
        parsed.warnings.insert(
            0,
            f"mineru_fallback_to_pymupdf: {type(exc).__name__}: {str(exc)[:240]}",
        )
        return DocumentParseResult(
            parsed=parsed,
            provider="pymupdf_fallback",
            version="fallback",
        )
