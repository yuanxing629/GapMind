"""End-to-end tests for the parse_pdf pipeline (Phase 2).

These tests verify the full flow:
  upload PDF -> Paper created with parse_status=pending
              -> parse_pdf task runs (synchronously via test patch)
              -> Paper.parse_status=parsed, chunk_count > 0
              -> parsed_text + chunk_index artifacts created
              -> chunks JSONL exported to data/chunks/{ws}/{paper}.jsonl
              -> timeline event "paper.parsed" recorded
              -> task row ends in "succeeded" status
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient


def _create_workspace(client: TestClient, name: str = "WS") -> dict:
    resp = client.post("/api/v1/workspaces", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_real_pdf(pages_text: list[str]) -> bytes:
    """Build a multi-page PDF with enough text to produce multiple chunks."""
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        rect = fitz.Rect(36, 36, page.rect.width - 36, page.rect.height - 36)
        page.insert_textbox(rect, text, fontsize=10, fontname="helv")
    return doc.tobytes()


# ----------------------------------------------------------------- upload
def test_upload_triggers_parse_pipeline(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    """Upload a PDF -> parsing happens automatically -> paper ends up parsed."""
    # Point storage to a tmp dir so the test doesn't pollute the repo.
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    monkeypatch.setattr(
        "app.workers.tasks.parse_pdf.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )

    ws = _create_workspace(client, "ParseWS")
    pdf = _make_real_pdf(
        [
            "Abstract\nWe propose a new method for GNN explainability. "
            + "This method uses mutual information. " * 30,
            "1. Introduction\nGraph neural networks are widely used. "
            + "We want to explain their predictions. " * 30,
            "2. Method\nOur approach optimizes a graph mask. "
            + "The mask retains important subgraphs. " * 30,
        ]
    )

    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("test.pdf", pdf, "application/pdf")},
        data={"title": "Test Paper", "authors": "Alice, Bob", "year": "2024"},
    )
    assert resp.status_code == 201, resp.text
    paper = resp.json()

    # After the sync spawn patch runs, the paper should be parsed.
    # Fetch the paper again to see the updated state.
    paper_resp = client.get(f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}")
    assert paper_resp.status_code == 200
    paper_after = paper_resp.json()
    assert paper_after["parse_status"] == "parsed"
    assert paper_after["chunk_count"] > 0
    assert paper_after["parsed_at"] is not None
    assert paper_after["parsed_text_artifact_id"] is not None
    assert paper_after["chunk_index_artifact_id"] is not None


def test_parse_creates_parsed_text_and_chunk_index_artifacts(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    monkeypatch.setattr(
        "app.workers.tasks.parse_pdf.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )

    ws = _create_workspace(client)
    pdf = _make_real_pdf(["This is page one. " * 80, "This is page two. " * 80])
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 201
    paper = resp.json()

    # List artifacts - should have 3: pdf, parsed_text, chunk_index
    arts = client.get(f"/api/v1/workspaces/{ws['id']}/artifacts").json()
    kinds = {a["kind"] for a in arts}
    assert "pdf" in kinds
    assert "parsed_text" in kinds
    assert "chunk_index" in kinds


def test_parse_exports_chunks_jsonl(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """The chunks JSONL file (Contract #1) is exported to data/chunks/{ws}/{paper}.jsonl."""
    storage_dir = tmp_path / "storage"
    data_dir = tmp_path / "data"
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(storage_dir),
    )
    monkeypatch.setattr(
        "app.workers.tasks.parse_pdf.settings.app_storage_dir",
        str(storage_dir),
    )
    # The export path is computed as storage_dir.parent / "data" / "chunks"
    # so it'll be tmp_path / "data" / "chunks".

    ws = _create_workspace(client)
    pdf = _make_real_pdf(["Page one content. " * 100, "Page two content. " * 100])
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 201
    paper = resp.json()

    # Find the exported JSONL file
    chunks_dir = data_dir / "chunks" / ws["id"]
    jsonl_path = chunks_dir / f"{paper['id']}.jsonl"
    assert jsonl_path.exists(), f"chunks JSONL not exported at {jsonl_path}"

    # Validate JSONL content
    lines = jsonl_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == paper["chunk_count"]
    for line in lines:
        chunk = json.loads(line)
        # Contract #1 required fields
        assert "chunk_id" in chunk
        assert "workspace_id" in chunk
        assert "paper_id" in chunk
        assert "artifact_id" in chunk
        assert "chunk_index" in chunk
        assert "text" in chunk
        assert "start_char" in chunk
        assert "end_char" in chunk
        assert "tokens_estimate" in chunk
        assert "chunk_version" in chunk
        assert "created_at" in chunk
        # Values should be sensible
        assert chunk["workspace_id"] == ws["id"]
        assert chunk["paper_id"] == paper["id"]
        assert chunk["chunk_id"]  # non-empty
        assert chunk["text"]  # non-empty
        assert chunk["tokens_estimate"] > 0


def test_parse_records_timeline_event(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    monkeypatch.setattr(
        "app.workers.tasks.parse_pdf.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )

    ws = _create_workspace(client)
    pdf = _make_real_pdf(["Some content here. " * 50])
    client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )

    timeline = client.get(f"/api/v1/workspaces/{ws['id']}/timeline").json()
    types = [e["event_type"] for e in timeline["items"]]
    assert "paper.uploaded" in types
    assert "paper.parsed" in types


def test_parse_task_ends_in_succeeded(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    monkeypatch.setattr(
        "app.workers.tasks.parse_pdf.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )

    ws = _create_workspace(client)
    pdf = _make_real_pdf(["Some content. " * 50])
    client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )

    tasks = client.get(f"/api/v1/workspaces/{ws['id']}/tasks").json()
    parse_tasks = [t for t in tasks["items"] if t["task_type"] == "parse_pdf"]
    assert len(parse_tasks) == 1
    assert parse_tasks[0]["status"] == "succeeded"
    assert parse_tasks[0]["progress"] == 1.0
    assert parse_tasks[0]["result"]["chunk_count"] > 0


def test_metadata_only_paper_not_parsed(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """A paper created without a PDF should NOT trigger parse_pdf."""
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    ws = _create_workspace(client)
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers",
        json={"title": "Meta Only", "authors": ["X"]},
    )
    assert resp.status_code == 201
    paper = resp.json()
    assert paper["parse_status"] == "not_applicable"
    assert paper["chunk_count"] == 0
    assert paper["parsed_text_artifact_id"] is None

    # No tasks should have been created.
    tasks = client.get(f"/api/v1/workspaces/{ws['id']}/tasks").json()
    assert tasks["total"] == 0


def test_attach_pdf_triggers_parse(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """Attaching a PDF to a metadata-only paper should trigger parse_pdf."""
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    monkeypatch.setattr(
        "app.workers.tasks.parse_pdf.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )

    ws = _create_workspace(client)
    # Create metadata-only
    paper = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers",
        json={"title": "Meta", "authors": ["X"]},
    ).json()
    assert paper["parse_status"] == "not_applicable"

    # Attach a PDF
    pdf = _make_real_pdf(["Some content. " * 80])
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}/upload-pdf",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 200
    paper_after = client.get(f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}").json()
    assert paper_after["parse_status"] == "parsed"
    assert paper_after["chunk_count"] > 0


def test_parse_failure_marks_paper_failed(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """If parsing fails (e.g. PDF has no text), paper.parse_status=failed."""
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    monkeypatch.setattr(
        "app.workers.tasks.parse_pdf.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )

    ws = _create_workspace(client)
    # Build a PDF with no text - just a blank page with an image-like shape
    doc = fitz.open()
    page = doc.new_page()
    page.draw_rect(fitz.Rect(36, 36, 100, 100), color=(0, 0, 0), fill=(1, 1, 1))
    empty_pdf = doc.tobytes()

    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("blank.pdf", empty_pdf, "application/pdf")},
    )
    assert resp.status_code == 201
    paper = resp.json()

    # Paper should be marked failed (no text was extracted).
    paper_after = client.get(f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}").json()
    assert paper_after["parse_status"] == "failed"
    assert paper_after["chunk_count"] == 0

    # Task should be in failed state with an error message.
    tasks = client.get(f"/api/v1/workspaces/{ws['id']}/tasks").json()
    parse_tasks = [t for t in tasks["items"] if t["task_type"] == "parse_pdf"]
    assert len(parse_tasks) == 1
    assert parse_tasks[0]["status"] == "failed"
    assert parse_tasks[0]["error"] is not None
