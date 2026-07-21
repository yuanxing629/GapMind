"""Tests for PDF metadata extraction + attach-PDF-to-existing flow."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domains.artifact.pdf_metadata import extract_metadata


def _create_workspace(client: TestClient, name: str = "WS") -> dict:
    resp = client.post("/api/v1/workspaces", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_pdf_with_metadata(title: str, author: str, year: int) -> bytes:
    """Build an in-memory PDF with embedded metadata using PyMuPDF."""
    import fitz

    doc = fitz.open()
    doc.insert_page(0, text="Hello world")
    doc.set_metadata(
        {
            "title": title,
            "author": author,
            "creationDate": f"D:{year}0315120000",
            "modDate": f"D:{year}0315120000",
        }
    )
    return doc.tobytes()


def _make_pdf_no_metadata() -> bytes:
    import fitz

    doc = fitz.open()
    doc.insert_page(0, text="No metadata here")
    return doc.tobytes()


# -------------------------------------------------------- pdf_metadata unit
def test_extract_metadata_reads_title_authors_year() -> None:
    pdf = _make_pdf_with_metadata("My Great Paper", "Alice; Bob", 2024)
    meta = extract_metadata(pdf)
    assert meta.title == "My Great Paper"
    assert meta.authors == ["Alice", "Bob"]
    assert meta.year == 2024
    assert meta.page_count == 1


def test_extract_metadata_handles_missing_fields() -> None:
    pdf = _make_pdf_no_metadata()
    meta = extract_metadata(pdf)
    assert meta.title is None
    assert meta.authors == []
    assert meta.year is None
    assert meta.page_count == 1


def test_extract_metadata_rejects_junk_title() -> None:
    import fitz

    doc = fitz.open()
    doc.insert_page(0, text="x")
    doc.set_metadata({"title": "Untitled", "author": "http://example.com"})
    meta = extract_metadata(doc.tobytes())
    assert meta.title is None
    assert meta.authors == []  # URL filtered out


def test_extract_metadata_splits_authors_on_various_separators() -> None:
    import fitz

    doc = fitz.open()
    doc.insert_page(0, text="x")
    doc.set_metadata({"author": "Alice, Bob and Carol"})
    meta = extract_metadata(doc.tobytes())
    assert meta.authors == ["Alice", "Bob", "Carol"]


def test_extract_metadata_invalid_pdf_returns_empty() -> None:
    meta = extract_metadata(b"not a pdf")
    assert meta.title is None
    assert meta.authors == []
    assert meta.year is None
    assert meta.page_count == 0


# ---------------------------------------------------- auto-fill on upload
def test_upload_auto_fills_metadata_from_pdf(client: TestClient) -> None:
    ws = _create_workspace(client)
    pdf = _make_pdf_with_metadata("Auto Filled Title", "Alice; Bob", 2023)

    # User supplies ONLY the file - no title/authors/year. Backend should
    # fill them from the PDF metadata.
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("auto.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Auto Filled Title"
    assert body["authors"] == ["Alice", "Bob"]
    assert body["year"] == 2023


def test_upload_user_supplied_values_override_pdf_metadata(client: TestClient) -> None:
    ws = _create_workspace(client)
    pdf = _make_pdf_with_metadata("PDF Title", "PDF Author", 2020)

    # User supplies explicit title/authors/year - those should win.
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("x.pdf", pdf, "application/pdf")},
        data={"title": "User Title", "authors": "Carol", "year": "2024"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "User Title"
    assert body["authors"] == ["Carol"]
    assert body["year"] == 2024


def test_upload_no_metadata_falls_back_to_filename(client: TestClient) -> None:
    ws = _create_workspace(client)
    pdf = _make_pdf_no_metadata()
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("Some-Paper-2022.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Some-Paper-2022"
    assert body["authors"] == []
    assert body["year"] is None


# ---------------------------------------------- attach PDF to existing
def test_attach_pdf_to_metadata_only_paper(client: TestClient) -> None:
    ws = _create_workspace(client)
    # Create a paper with metadata only.
    paper = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers",
        json={"title": "Manual Title", "authors": ["Existing Author"]},
    ).json()
    assert paper["primary_artifact_id"] is None

    # Now attach a PDF with different metadata. Since the paper already has
    # title/authors, those are NOT overwritten; only empty fields get filled.
    pdf = _make_pdf_with_metadata("PDF Title", "PDF Author", 2024)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}/upload-pdf",
        files={"file": ("attached.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["primary_artifact_id"] is not None
    # Existing metadata preserved.
    assert body["title"] == "Manual Title"
    assert body["authors"] == ["Existing Author"]
    # Year was empty -> filled from PDF.
    assert body["year"] == 2024

    # Timeline records the attach event.
    timeline = client.get(f"/api/v1/workspaces/{ws['id']}/timeline").json()
    types = [e["event_type"] for e in timeline["items"]]
    assert "paper.pdf_attached" in types


def test_attach_pdf_fills_empty_fields(client: TestClient) -> None:
    ws = _create_workspace(client)
    # Create a paper with only a title, no authors/year.
    paper = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers",
        json={"title": "T"},
    ).json()

    pdf = _make_pdf_with_metadata("Ignored", "Alice, Bob", 2022)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}/upload-pdf",
        files={"file": ("x.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "T"  # preserved
    assert body["authors"] == ["Alice", "Bob"]  # filled
    assert body["year"] == 2022  # filled


def test_attach_pdf_to_paper_that_already_has_pdf_returns_409(client: TestClient) -> None:
    ws = _create_workspace(client)
    pdf1 = _make_pdf_with_metadata("T1", "A1", 2020)
    paper = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("p1.pdf", pdf1, "application/pdf")},
    ).json()

    pdf2 = _make_pdf_with_metadata("T2", "A2", 2021)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}/upload-pdf",
        files={"file": ("p2.pdf", pdf2, "application/pdf")},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "paper_already_has_pdf"


def test_attach_pdf_to_missing_paper_returns_404(client: TestClient) -> None:
    ws = _create_workspace(client)
    pdf = _make_pdf_with_metadata("T", "A", 2020)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/00000000-0000-0000-0000-000000000000/upload-pdf",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 404


def test_attach_pdf_cross_workspace_returns_404(client: TestClient) -> None:
    ws_a = _create_workspace(client, "A")
    ws_b = _create_workspace(client, "B")
    paper = client.post(
        f"/api/v1/workspaces/{ws_a['id']}/papers",
        json={"title": "T"},
    ).json()
    pdf = _make_pdf_with_metadata("T", "A", 2020)
    resp = client.post(
        f"/api/v1/workspaces/{ws_b['id']}/papers/{paper['id']}/upload-pdf",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 404
