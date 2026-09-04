"""本地 MinerU 适配器及其 ParsedPdf 规范化测试。"""

from __future__ import annotations

import io
import json
import zipfile

import httpx

from app.domains.artifact.document_parser import parse_document
from app.domains.artifact.mineru_parser import (
    MinerUUnavailableError,
    normalize_mineru_output,
    parse_with_mineru,
)
from app.domains.artifact.pdf_parser import ParsedPdf


def test_normalize_legacy_content_list_preserves_pages_and_sections() -> None:
    result = normalize_mineru_output(
        {
            "paper_content_list.json": json.dumps(
                [
                    {
                        "type": "text",
                        "text": "1 Introduction",
                        "text_level": 1,
                        "page_idx": 0,
                    },
                    {
                        "type": "text",
                        "text": "MinerU keeps the reading order.",
                        "page_idx": 0,
                    },
                    {
                        "type": "equation",
                        "text": "x = y",
                        "page_idx": 1,
                    },
                ]
            ).encode(),
            "paper_middle.json": json.dumps(
                {"_backend": "pipeline", "_version_name": "3.0.0"}
            ).encode(),
            "paper.md": b"# 1 Introduction\n\nMinerU keeps the reading order.",
        }
    )

    parsed = result.parsed
    assert result.backend == "pipeline"
    assert result.version == "3.0.0"
    assert parsed.page_count == 2
    assert "MinerU keeps" in parsed.full_text
    assert "\fx = y" in parsed.full_text
    assert parsed.sections[0].section == "Introduction"
    assert parsed.sections[0].page_number == 1
    assert parsed.to_markdown().startswith("# 1 Introduction")


def test_normalize_v2_content_list_extracts_structured_content() -> None:
    result = normalize_mineru_output(
        {
            "paper_content_list_v2.json": json.dumps(
                [
                    [
                        {
                            "type": "title",
                            "content": {
                                "title_content": [
                                    {"type": "text", "content": "2 Method"}
                                ],
                                "level": 1,
                            },
                        },
                        {
                            "type": "paragraph",
                            "content": {
                                "paragraph_content": [
                                    {"type": "text", "content": "A robust method."}
                                ]
                            },
                        },
                    ],
                    [
                        {
                            "type": "table",
                            "content": {
                                "table_body": "<table><tr><td>score</td></tr></table>"
                            },
                        }
                    ],
                ]
            ).encode(),
            "paper.md": b"# 2 Method\n\nA robust method.",
        }
    )

    assert result.parsed.page_count == 2
    assert "A robust method." in result.parsed.full_text
    assert "score" in result.parsed.full_text
    assert result.parsed.sections[0].section == "Method"


def test_normalize_strips_mineru_layout_tags_but_keeps_content() -> None:
    result = normalize_mineru_output(
        {
            "paper_content_list.json": json.dumps(
                [
                    {
                        "type": "text",
                        "text": "Ex<sub>p</sub>lainin<sub>g</sub> Graph",
                        "page_idx": 0,
                    },
                    {
                        "type": "equation",
                        "text": "x<sub>i</sub> = y<sup>2</sup>",
                        "page_idx": 0,
                    },
                ]
            ).encode(),
            "paper.md": (
                b"# Ex<sub>p</sub>lainin<sub>g</sub> Graph\n\n"
                b"$$x<sub>i</sub> = y<sup>2</sup>$$"
            ),
        }
    )

    assert result.parsed.full_text == "Explaining Graph\n\nxi = y2"
    assert "<sub>" not in result.parsed.full_text
    assert "<sup>" not in result.parsed.full_text
    assert "Explaining Graph" in result.parsed.to_markdown()
    assert "<sub>" not in result.parsed.to_markdown()
    assert "$$xi = y2$$" in result.parsed.to_markdown()


def test_normalize_can_remove_unpersisted_image_references() -> None:
    result = normalize_mineru_output(
        {
            "paper_content_list.json": json.dumps(
                [{"type": "text", "text": "Figure description", "page_idx": 0}]
            ).encode(),
            "paper.md": b"![](images/figure-1.jpg)\n\nFigure 1: Figure description",
            "paper/images/figure-1.jpg": b"jpeg-bytes",
        },
        include_images=False,
    )

    assert "images/figure-1.jpg" not in result.parsed.to_markdown()
    assert "Figure 1: Figure description" in result.parsed.to_markdown()
    assert result.images == ()


def test_parse_with_mineru_posts_pdf_and_reads_zip_result() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "paper_content_list.json",
            json.dumps(
                [{"type": "text", "text": "Parsed by MinerU", "page_idx": 0}]
            ),
        )
        archive.writestr(
            "paper.md",
            "Parsed by MinerU\n\n![](images/figure-1.jpg)\n\nFigure 1: Figure description",
        )
        archive.writestr("paper/images/figure-1.jpg", b"jpeg-bytes")

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            content=output.getvalue(),
            request=request,
        )

    def client_factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    result = parse_with_mineru(
        b"%PDF-test",
        api_url="http://mineru.test",
        client_factory=client_factory,
    )

    assert result.parsed.full_text == "Parsed by MinerU"
    assert result.images == ()
    assert "images/figure-1.jpg" not in result.parsed.to_markdown()
    assert len(requests) == 1
    assert requests[0].url.path == "/file_parse"
    request_body = requests[0].content
    assert b"document.pdf" in request_body
    for field, expected in {
        "backend": "pipeline",
        "parse_method": "auto",
        "return_content_list": "true",
        "return_middle_json": "true",
        "image_analysis": "false",
        "return_images": "false",
    }.items():
        field_start = request_body.index(f'name="{field}"'.encode())
        value_start = request_body.index(b"\r\n\r\n", field_start) + 4
        value_end = request_body.index(b"\r\n", value_start)
        assert request_body[value_start:value_end] == expected.encode()


def test_parse_with_mineru_uses_clean_pymupdf_text_backstop(monkeypatch) -> None:
    from app.domains.artifact import mineru_parser

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "paper_content_list.json",
            json.dumps(
                [{"type": "text", "text": "diference efectiveness", "page_idx": 0}]
            ),
        )
        archive.writestr("paper.md", "diference efectiveness")

    clean_text = "difference effectiveness"
    monkeypatch.setattr(
        mineru_parser,
        "parse_pdf",
        lambda content: ParsedPdf(
            full_text=clean_text,
            page_count=1,
            page_char_ranges=[(0, len(clean_text))],
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/zip"},
            content=output.getvalue(),
            request=request,
        )

    def client_factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    result = parse_with_mineru(
        b"%PDF-test",
        api_url="http://mineru.test",
        client_factory=client_factory,
    )

    assert result.parsed.full_text == clean_text
    assert "difference effectiveness" in result.parsed.to_markdown()
    assert "mineru_plain_text_replaced_with_pymupdf" in result.parsed.warnings


def test_parse_document_falls_back_to_pymupdf(monkeypatch) -> None:
    from app.domains.artifact import document_parser

    monkeypatch.setattr(document_parser.settings, "parser_provider", "mineru_local")
    monkeypatch.setattr(
        document_parser.settings,
        "parser_fallback_enabled",
        True,
    )
    monkeypatch.setattr(
        document_parser,
        "parse_with_mineru",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            MinerUUnavailableError("service is down")
        ),
    )
    monkeypatch.setattr(
        document_parser,
        "parse_pdf",
        lambda content: ParsedPdf(
            full_text="fallback text",
            page_count=1,
            page_char_ranges=[(0, 13)],
        ),
    )

    result = parse_document(b"pdf")

    assert result.provider == "pymupdf_fallback"
    assert result.parsed.full_text == "fallback text"
    assert result.parsed.warnings[0].startswith("mineru_fallback_to_pymupdf")
