"""parse_pdf 流水线的端到端测试（Phase 2）。

这些测试验证完整流程：
  上传 PDF -> 创建 parse_status=pending 的 Paper
            -> parse_pdf task 运行（通过测试替换同步执行）
            -> Paper.parse_status=parsed，chunk_count > 0
            -> 创建 parsed_text + chunk_index artifact
            -> 将 chunk_index JSONL 保存为 canonical storage Artifact
            -> 记录 timeline event "paper.parsed"
            -> task 行最终为 "succeeded" 状态
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz
from fastapi.testclient import TestClient

from app.domains.artifact.document_parser import DocumentParseResult
from app.domains.artifact.mineru_parser import MinerUImage
from app.domains.artifact.pdf_parser import ParsedPdf


def _create_workspace(client: TestClient, name: str = "WS") -> dict:
    resp = client.post("/api/v1/workspaces", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _make_real_pdf(pages_text: list[str]) -> bytes:
    """构造包含足够文本、可以生成多个分块的多页 PDF。"""
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        rect = fitz.Rect(36, 36, page.rect.width - 36, page.rect.height - 36)
        page.insert_textbox(rect, text, fontsize=10, fontname="helv")
    return doc.tobytes()


# ----------------------------------------------------------------- 上传
def test_upload_triggers_parse_pipeline(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    """上传 PDF -> 自动执行解析 -> 论文最终进入已解析状态。"""
# 将存储指向临时目录，避免测试污染仓库。
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
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

# 同步 spawn patch 执行后，论文应完成解析。
# 再次获取论文，检查更新后的状态。
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
    ws = _create_workspace(client)
    pdf = _make_real_pdf(["This is page one. " * 80, "This is page two. " * 80])
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 201
    paper = resp.json()

# 列出 artifacts，PDF 和全部派生 artifact 都应属于该论文目录。
    arts = client.get(f"/api/v1/workspaces/{ws['id']}/artifacts").json()
    kinds = {a["kind"] for a in arts}
    assert "pdf" in kinds
    assert "parsed_text" in kinds
    assert "chunk_index" in kinds
    expected_prefix = (
        f"workspaces/{ws['id'][:2]}/{ws['id']}/papers/{paper['id']}/artifacts/"
    )
    assert all(a["file_path"].startswith(expected_prefix) for a in arts)


def test_parse_persists_mineru_image_resources(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )

    def fake_parse_document(content: bytes) -> DocumentParseResult:
        assert content.startswith(b"%PDF-")
        parsed = ParsedPdf(
            full_text="Paper text",
            page_count=1,
            page_char_ranges=[(0, len("Paper text"))],
        )
        return DocumentParseResult(
            parsed=parsed,
            provider="mineru_local",
            backend="pipeline",
            version="3.4.5",
            images=(
                MinerUImage(
                    relative_path="images/figure-1.jpg",
                    content=b"jpeg-bytes",
                    mime_type="image/jpeg",
                ),
            ),
        )

    monkeypatch.setattr(
        "app.workers.tasks.parse_pdf.parse_document",
        fake_parse_document,
    )
    ws = _create_workspace(client)
    pdf = _make_real_pdf(["Paper text"])
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    paper = resp.json()

    image_response = client.get(
        f"/api/v1/workspaces/{ws['id']}/artifacts",
        params={"kind": "paper_image", "paper_id": paper["id"]},
    )
    assert image_response.status_code == 200, image_response.text
    images = image_response.json()
    assert len(images) == 1
    image = images[0]
    assert image["original_filename"] == "figure-1.jpg"
    assert image["mime_type"] == "image/jpeg"
    assert image["file_path"].startswith(
        f"workspaces/{ws['id'][:2]}/{ws['id']}/papers/{paper['id']}/artifacts/"
    )

    download = client.get(
        f"/api/v1/workspaces/{ws['id']}/artifacts/{image['id']}/download"
    )
    assert download.status_code == 200
    assert download.content == b"jpeg-bytes"


def test_parse_persists_chunk_index_artifact(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """可以从 storage Artifact 读取规范的分块 JSONL。"""
    storage_dir = tmp_path / "storage"
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(storage_dir),
    )
    ws = _create_workspace(client)
    pdf = _make_real_pdf(["Page one content. " * 100, "Page two content. " * 100])
    resp = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers/upload",
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )
    assert resp.status_code == 201
    paper = resp.json()

    chunk_artifacts = client.get(
        f"/api/v1/workspaces/{ws['id']}/artifacts",
        params={"kind": "chunk_index"},
    ).json()
    assert len(chunk_artifacts) == 1
    chunk_artifact = chunk_artifacts[0]
    assert chunk_artifact["id"] == paper["chunk_index_artifact_id"]

# 校验从规范 Artifact 提供的 JSONL 内容。
    download = client.get(
        f"/api/v1/workspaces/{ws['id']}/artifacts/{chunk_artifact['id']}/download"
    )
    assert download.status_code == 200, download.text
    lines = download.text.strip().split("\n")
    assert len(lines) == paper["chunk_count"]
    for line in lines:
        chunk = json.loads(line)
# 契约 #1 的必需字段
        assert "chunk_id" in chunk
        assert "workspace_id" in chunk
        assert "paper_id" in chunk
        assert chunk["schema_version"] == "1.0.0"
        assert "source_artifact_id" in chunk
        assert chunk["source_artifact_kind"] == "parsed_text"
        assert "chunk_index" in chunk
        assert "text" in chunk
        assert "start_char" in chunk
        assert "end_char" in chunk
        assert "tokens_estimate" in chunk
        assert "chunk_version" in chunk
        assert "created_at" in chunk
# 字段值应合理
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
    """未附带 PDF 创建论文时，不应触发 parse_pdf。"""
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

# 不应创建任何任务。
    tasks = client.get(f"/api/v1/workspaces/{ws['id']}/tasks").json()
    assert tasks["total"] == 0


def test_attach_pdf_triggers_parse(
    client: TestClient, tmp_path: Path, monkeypatch
) -> None:
    """向只有元数据的论文附加 PDF 时，应触发 parse_pdf。"""
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    ws = _create_workspace(client)
# 创建仅元数据论文
    paper = client.post(
        f"/api/v1/workspaces/{ws['id']}/papers",
        json={"title": "Meta", "authors": ["X"]},
    ).json()
    assert paper["parse_status"] == "not_applicable"

# 附加 PDF
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
    """如果解析失败（例如 PDF 没有文本），paper.parse_status 应为 failed。"""
    monkeypatch.setattr(
        "app.domains.artifact.service.settings.app_storage_dir",
        str(tmp_path / "storage"),
    )
    ws = _create_workspace(client)
# 构造没有文本的 PDF，仅包含一个类似图片的空白图形
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

# 论文应标记为 failed（没有提取到文本）。
    paper_after = client.get(f"/api/v1/workspaces/{ws['id']}/papers/{paper['id']}").json()
    assert paper_after["parse_status"] == "failed"
    assert paper_after["chunk_count"] == 0

# Task 应处于 failed 状态，并包含错误消息。
    tasks = client.get(f"/api/v1/workspaces/{ws['id']}/tasks").json()
    parse_tasks = [t for t in tasks["items"] if t["task_type"] == "parse_pdf"]
    assert len(parse_tasks) == 1
    assert parse_tasks[0]["status"] == "failed"
    assert parse_tasks[0]["error"] is not None
