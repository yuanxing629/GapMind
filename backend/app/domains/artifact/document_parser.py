"""论文导入流水线的解析器选择。"""

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
    """由现有 parse 任务消费的、与解析器无关的结果。"""

    parsed: ParsedPdf
    provider: str
    backend: str | None = None
    version: str | None = None
    images: tuple[MinerUImage, ...] = ()


def parse_document(content: bytes) -> DocumentParseResult:
    """使用配置的 provider 解析，必要时回退到本地解析。"""
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
