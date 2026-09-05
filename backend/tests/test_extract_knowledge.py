"""Phase 3 抽取安全性与来源追溯测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.domains.artifact.models import Artifact
from app.domains.artifact.service import ArtifactService
from app.domains.knowledge.models import (
    CanonicalEntity,
    EvidenceSpan,
    ExtractionRejection,
    ExtractionRun,
    KnowledgeItem,
)
from app.domains.knowledge.schemas import (
    ExtractionOutput,
    ExtractionRejectionCreate,
)
from app.domains.knowledge.service import KnowledgeService
from app.domains.paper.models import Paper
from app.domains.task.schemas import TaskCreate
from app.domains.task.schemas import summarize_task_error
from app.domains.task.service import TaskService
from app.domains.workspace.models import Workspace
from app.workers.tasks import extract_knowledge as extraction_module
from app.workers.tasks.extract_knowledge import (
    _normalize_relation_type,
    _run_extract,
    _validate_and_rebase_evidence,
    _write_extraction,
    extract_knowledge_task,
)
from app.workers.tasks.extraction.batching import split_extraction_batches as _split_extraction_batches


def _id() -> str:
    return str(uuid4())


def _workspace(db: Session) -> Workspace:
    ws = Workspace(
        id=_id(),
        name="Extraction test",
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    db.add(ws)
    db.commit()
    return ws


def _paper_with_markdown(
    db: Session, workspace_id: str, *, title: str = "Paper"
) -> tuple[Paper, Artifact]:
    artifact = Artifact(
        id=_id(),
        workspace_id=workspace_id,
        kind="parsed_markdown",
        file_path=f"{_id()}.md",
        mime_type="text/markdown",
        size_bytes=100,
        is_deleted=False,
    )
    paper = Paper(
        id=_id(),
        workspace_id=workspace_id,
        title=title,
        authors=[],
        source="manual",
        parse_status="parsed",
        chunk_count=0,
        parsed_markdown_artifact_id=artifact.id,
        extract_status="pending",
        is_deleted=False,
    )
    db.add_all([artifact, paper])
    db.commit()
    return paper, artifact


def _run(
    db: Session, workspace_id: str, paper: Paper, artifact: Artifact
) -> ExtractionRun:
    task = TaskService(db).create(
        TaskCreate(
            workspace_id=workspace_id,
            task_type="extract_knowledge",
            payload={"paper_id": paper.id},
        )
    )
    run = ExtractionRun(
        id=_id(),
        workspace_id=workspace_id,
        paper_id=paper.id,
        artifact_id=artifact.id,
        task_id=task.id,
        schema_version="1.0.0",
        prompt_version="extract_v2",
        model_provider="test",
        model_name="test-model",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.commit()
    return run


def _method_item(start: int = 0, end: int = 23) -> dict:
    return {
        "type": "method",
        "canonical_name": "GNNExplainer",
        "content": {
            "description": "An explanation method.",
            "problem_addressed": "Explain predictions.",
            "inputs": ["graph"],
            "outputs": ["subgraph"],
            "key_idea": "Optimize a mask.",
            "training_paradigm": "post-hoc",
            "computational_cost": "moderate",
            "code_repository": None,
        },
        "source_provenance": {
            "start_char": start,
            "end_char": end,
            "batch_index": 0,
        },
        "evidence_text": "GNNExplainer is used.",
        "confidence": 0.9,
    }


def test_evidence_offset_is_repaired_only_by_exact_match() -> None:
    text = "Introduction.\nGNNExplainer is used.\nConclusion."
    output = ExtractionOutput.model_validate(
        {
            "items": [
                {
                    **_method_item(),
                    "source_provenance": {"start_char": 0, "end_char": 2},
                }
            ],
            "relations": [],
        }
    )
    items, _ = _validate_and_rebase_evidence(
        items=output.items,
        paper_text=text,
        batch_text=text,
        batch_start=0,
        batch_index=0,
    )
    expected_start = text.index("GNNExplainer is used.")
    assert items[0]["source_provenance"]["start_char"] == expected_start
    assert items[0]["source_provenance"]["end_char"] == expected_start + len(
        "GNNExplainer is used."
    )


def test_zero_offsets_and_uses_relation_are_tolerated() -> None:
    output = ExtractionOutput.model_validate(
        {
            "items": [
                {
                    **_method_item(),
                    "source_provenance": {"start_char": 0, "end_char": 0},
                }
            ],
            "relations": [
                {
                    "source_type": "method",
                    "source_name": "GNNExplainer",
                    "relation": "uses",
                    "target_type": "dataset",
                    "target_name": "MUTAG",
                }
            ],
        }
    )
    assert output.items[0].source_provenance.end_char == 0
    assert _normalize_relation_type(output.relations[0].model_dump()) == "evaluates_on"


def test_task_error_summary_hides_validation_details() -> None:
    raw = (
        "24 validation errors for ExtractionOutput\n"
        "items.0.method.source_provenance.end_char\n"
        "Input should be greater than 0"
    )
    summary = summarize_task_error(raw)
    assert summary == (
        "Knowledge extraction returned an invalid structure. "
        "Retry the extraction or inspect worker logs."
    )
    assert "items.0" not in summary


def test_evidence_whitespace_difference_uses_exact_artifact_slice() -> None:
    text = "Introduction.\nGNNExplainer\n\nis used.\nConclusion."
    output = ExtractionOutput.model_validate(
        {
            "items": [
                {
                    **_method_item(),
                    "source_provenance": {"start_char": 0, "end_char": 2},
                }
            ],
            "relations": [],
        }
    )
    items, _ = _validate_and_rebase_evidence(
        items=output.items,
        paper_text=text,
        batch_text=text,
        batch_start=0,
        batch_index=0,
    )
    assert len(items) == 1
    assert items[0]["evidence_text"] == "GNNExplainer\n\nis used."


def test_evidence_layout_normalization_uses_exact_artifact_slice() -> None:
    text = "ProtGNN uses a ﬁxed prototype—based explanation."
    evidence = "ProtGNN uses a fixed prototype-based explanation."
    output = ExtractionOutput.model_validate(
        {
            "items": [
                {
                    **_method_item(),
                    "evidence_text": evidence,
                    "source_provenance": {"start_char": 0, "end_char": 0},
                }
            ],
            "relations": [],
        }
    )

    items, _ = _validate_and_rebase_evidence(
        items=output.items,
        paper_text=text,
        batch_text=text,
        batch_start=0,
        batch_index=0,
    )

    assert items[0]["evidence_text"] == text
    assert items[0]["source_provenance"] == {
        "start_char": 0,
        "end_char": len(text),
        "batch_index": 0,
    }


def test_evidence_pdf_line_hyphenation_uses_exact_artifact_slice() -> None:
    text = "The non-\ninterpretable baseline is compared."
    evidence = "The non-interpretable baseline is compared."
    output = ExtractionOutput.model_validate(
        {
            "items": [
                {
                    **_method_item(),
                    "evidence_text": evidence,
                    "source_provenance": {"start_char": 0, "end_char": 0},
                }
            ],
            "relations": [],
        }
    )

    items, _ = _validate_and_rebase_evidence(
        items=output.items,
        paper_text=text,
        batch_text=text,
        batch_start=0,
        batch_index=0,
    )

    assert items[0]["evidence_text"] == text


def test_duplicate_exact_evidence_uses_nearest_reported_position() -> None:
    evidence = "GNNExplainer is used."
    text = f"{evidence}\nOther text.\n{evidence}"
    second_start = text.rindex(evidence)
    output = ExtractionOutput.model_validate(
        {
            "items": [
                {
                    **_method_item(),
                    "source_provenance": {
                        "start_char": second_start + 2,
                        "end_char": second_start + len(evidence) + 2,
                    },
                }
            ],
            "relations": [],
        }
    )
    items, _ = _validate_and_rebase_evidence(
        items=output.items,
        paper_text=text,
        batch_text=text,
        batch_start=0,
        batch_index=0,
    )
    assert items[0]["source_provenance"]["start_char"] == second_start


def test_one_bad_evidence_does_not_reject_valid_items() -> None:
    text = "GNNExplainer is used."
    output = ExtractionOutput.model_validate(
        {
            "items": [
                {
                    **_method_item(start=0, end=len(text)),
                    "source_provenance": {
                        "start_char": 0,
                        "end_char": len(text),
                    },
                },
                {
                    **_method_item(),
                    "canonical_name": "Unsupported method",
                    "evidence_text": "This sentence does not exist.",
                    "source_provenance": {"start_char": 0, "end_char": 29},
                },
            ],
            "relations": [],
        }
    )
    items, _ = _validate_and_rebase_evidence(
        items=output.items,
        paper_text=text,
        batch_text=text,
        batch_start=0,
        batch_index=0,
    )
    assert [item["canonical_name"] for item in items] == ["GNNExplainer"]


def test_long_document_batches_cover_conclusion() -> None:
    text = ("A" * 39000) + "\n\n## Method\n" + ("B" * 39000) + "\n\n## Conclusion\nKEY"
    batches = _split_extraction_batches(text, max_chars=40000, overlap_chars=500)
    assert len(batches) >= 2
    assert batches[0][0] == 0
    last_start, last_text = batches[-1]
    assert last_start + len(last_text) == len(text)
    assert "## Conclusion\nKEY" in last_text


def test_same_entity_keeps_independent_paper_mentions(db_session: Session) -> None:
    ws = _workspace(db_session)
    paper1, artifact1 = _paper_with_markdown(db_session, ws.id, title="P1")
    paper2, artifact2 = _paper_with_markdown(db_session, ws.id, title="P2")
    run1 = _run(db_session, ws.id, paper1, artifact1)
    run2 = _run(db_session, ws.id, paper2, artifact2)

    assert _write_extraction(
        db_session, paper1, run1, [_method_item()], []
    ) == (1, 0, 1, 0)
    db_session.commit()
    assert _write_extraction(
        db_session, paper2, run2, [_method_item()], []
    ) == (1, 0, 1, 0)
    db_session.commit()

    entity_count = db_session.execute(
        select(func.count()).select_from(CanonicalEntity)
    ).scalar_one()
    mentions = list(
        db_session.execute(
            select(KnowledgeItem).order_by(KnowledgeItem.paper_id)
        ).scalars()
    )
    assert entity_count == 1
    assert len(mentions) == 2
    assert {item.paper_id for item in mentions} == {paper1.id, paper2.id}
    assert mentions[0].canonical_entity_id == mentions[1].canonical_entity_id

    # 重试同一运行是幂等的。
    _write_extraction(db_session, paper1, run1, [_method_item()], [])
    db_session.commit()
    assert db_session.execute(
        select(func.count()).select_from(KnowledgeItem)
    ).scalar_one() == 2
    assert db_session.execute(
        select(func.count()).select_from(EvidenceSpan)
    ).scalar_one() == 2


def test_unresolved_relation_is_persisted_as_rejection(
    db_session: Session,
) -> None:
    ws = _workspace(db_session)
    paper, artifact = _paper_with_markdown(db_session, ws.id)
    run = _run(db_session, ws.id, paper, artifact)
    counts = _write_extraction(
        db_session,
        paper,
        run,
        [_method_item()],
        [
            {
                "source_type": "method",
                "source_name": "GNNExplainer",
                "relation": "uses",
                "target_type": "dataset",
                "target_name": "Missing dataset",
                "confidence": 0.8,
            }
        ],
    )
    db_session.commit()
    assert counts == (1, 0, 1, 1)
    rejection = db_session.execute(
        select(ExtractionRejection)
    ).scalar_one()
    assert rejection.rejection_kind == "relation"
    assert rejection.reason_code == "unresolved_endpoint"


def test_invalid_item_is_rejected_without_losing_valid_item(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    ws = _workspace(db_session)
    paper, artifact = _paper_with_markdown(db_session, ws.id)
    task = TaskService(db_session).create(
        TaskCreate(
            workspace_id=ws.id,
            task_type="extract_knowledge",
            payload={"paper_id": paper.id},
        )
    )
    markdown_path = tmp_path / "paper.md"
    markdown_path.write_text(
        "GNNExplainer is used.\nA limitation is reported.", encoding="utf-8"
    )
    monkeypatch.setattr(
        ArtifactService, "resolve_abs_path", lambda self, value: markdown_path
    )
    invalid_output = {
        "items": [
            {
                **_method_item(),
                "source_provenance": {"start_char": 0, "end_char": 21},
            },
            {
                "type": "method",
                "canonical_name": "Broken",
                "content": {
                    "description": "Missing required fields",
                    "problem_addressed": "test",
                    "inputs": [],
                    "outputs": [],
                },
                "source_provenance": {"start_char": 0, "end_char": 21},
                "evidence_text": "GNNExplainer is used.",
            },
        ],
        "relations": [],
    }
    monkeypatch.setattr(
        extraction_module,
        "call_llm_with_retry",
        lambda messages, max_retries: ("{}", invalid_output),
    )

    result = _run_extract(db_session, task.id)

    assert result["status"] == "succeeded"
    assert db_session.execute(
        select(func.count()).select_from(KnowledgeItem)
    ).scalar_one() == 1
    assert db_session.execute(
        select(func.count()).select_from(ExtractionRejection)
    ).scalar_one() == 1
    assert result["rejected_schema"] == 1
    db_session.refresh(task)
    db_session.refresh(paper)
    assert task.status == "succeeded"
    assert paper.extract_status == "extracted"


def test_all_invalid_items_fail_but_keep_rejections(
    db_session: Session, tmp_path: Path, monkeypatch
) -> None:
    ws = _workspace(db_session)
    paper, _artifact = _paper_with_markdown(db_session, ws.id)
    task = TaskService(db_session).create(
        TaskCreate(
            workspace_id=ws.id,
            task_type="extract_knowledge",
            payload={"paper_id": paper.id},
        )
    )
    markdown_path = tmp_path / "paper.md"
    markdown_path.write_text("Some source text.", encoding="utf-8")
    monkeypatch.setattr(
        ArtifactService, "resolve_abs_path", lambda self, value: markdown_path
    )
    monkeypatch.setattr(
        extraction_module,
        "call_llm_with_retry",
        lambda messages, max_retries: (
            "{}",
            {
                "items": [
                    {
                        "type": "method",
                        "canonical_name": "Broken",
                        "content": {"description": "Incomplete"},
                        "source_provenance": {"start_char": 0, "end_char": 0},
                        "evidence_text": "Some source text.",
                    }
                ],
                "relations": [],
            },
        ),
    )

    result = _run_extract(db_session, task.id)

    assert result["status"] == "failed"
    assert result["rejected_total"] == 1
    assert db_session.execute(
        select(func.count()).select_from(KnowledgeItem)
    ).scalar_one() == 0
    assert db_session.execute(
        select(func.count()).select_from(ExtractionRejection)
    ).scalar_one() == 1


def test_rejection_is_idempotent_and_workspace_scoped(
    client: TestClient, db_session: Session
) -> None:
    ws = _workspace(db_session)
    other_ws = _workspace(db_session)
    paper, artifact = _paper_with_markdown(db_session, ws.id)
    run = _run(db_session, ws.id, paper, artifact)
    payload = ExtractionRejectionCreate(
        workspace_id=ws.id,
        extraction_run_id=run.id,
        paper_id=paper.id,
        batch_index=0,
        rejection_kind="item",
        stage="evidence_resolution",
        reason_code="evidence_not_found",
        reason_detail="No exact source span.",
        item_type="claim",
        canonical_name="Unsupported claim",
        raw_payload={"type": "claim", "statement": "unsupported"},
        evidence_preview="unsupported",
    )
    service = KnowledgeService(db_session)
    first = service.create_rejection(payload)
    second = service.create_rejection(payload)
    service.create_rejection(
        payload.model_copy(
            update={
                "reason_code": "ambiguous_evidence",
                "raw_payload": {"type": "claim", "statement": "ambiguous"},
            }
        )
    )
    db_session.commit()
    assert first.id == second.id

    response = client.get(
        f"/api/v1/workspaces/{ws.id}/extraction-runs/{run.id}/rejections",
        params={
            "stage": "evidence_resolution",
            "reason_code": "evidence_not_found",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["raw_payload"]["type"] == "claim"

    page = client.get(
        f"/api/v1/workspaces/{ws.id}/extraction-runs/{run.id}/rejections",
        params={"limit": 1, "offset": 1},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 2
    assert len(page.json()["items"]) == 1

    cross_workspace = client.get(
        f"/api/v1/workspaces/{other_ws.id}/extraction-runs/{run.id}/rejections"
    )
    assert cross_workspace.status_code == 404


def test_manual_extraction_trigger_requires_parsed_markdown(
    client: TestClient, db_session: Session
) -> None:
    ws_response = client.post("/api/v1/workspaces", json={"name": "Trigger"})
    workspace_id = ws_response.json()["id"]
    paper_response = client.post(
        f"/api/v1/workspaces/{workspace_id}/papers",
        json={"title": "Metadata only"},
    )
    paper_id = paper_response.json()["id"]

    not_ready = client.post(
        f"/api/v1/workspaces/{workspace_id}/papers/{paper_id}/extract"
    )
    assert not_ready.status_code == 409
    assert not_ready.json()["detail"]["error"] == "paper_not_parsed"

    artifact = Artifact(
        id=_id(),
        workspace_id=workspace_id,
        kind="parsed_markdown",
        file_path="test.md",
        mime_type="text/markdown",
        size_bytes=10,
        is_deleted=False,
    )
    paper = db_session.get(Paper, paper_id)
    db_session.add(artifact)
    db_session.flush()
    paper.parsed_markdown_artifact_id = artifact.id
    paper.parse_status = "parsed"
    db_session.commit()

    accepted = client.post(
        f"/api/v1/workspaces/{workspace_id}/papers/{paper_id}/extract"
    )
    assert accepted.status_code == 202
    assert accepted.json() == {
        "task_id": "test-extraction-task",
        "status": "queued",
    }


def test_celery_task_raises_when_business_task_failed(monkeypatch) -> None:
    db = MagicMock()
    monkeypatch.setattr(extraction_module, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        extraction_module,
        "_run_extract",
        lambda session, task_id: {"status": "failed", "error": "bad evidence"},
    )

    with pytest.raises(RuntimeError, match="bad evidence"):
        extract_knowledge_task.run("task-id")
    db.close.assert_called_once()


# ==================================================================
# P0 精确去重集成测试
# ==================================================================


def _claim_item(
    statement: str = "PGIB outperforms state-of-the-art methods.",
    start: int = 100,
    end: int = 200,
    confidence: float = 0.9,
) -> dict:
    return {
        "type": "claim",
        "canonical_name": "PGIB outperforms SOTA",
        "content": {"statement": statement},
        "source_provenance": {"start_char": start, "end_char": end, "batch_index": 0},
        "evidence_text": statement,
        "confidence": confidence,
    }


def _limitation_item(
    description: str = "PGIB lacks domain knowledge integration.",
    start: int = 100,
    end: int = 200,
    confidence: float = 0.6,
) -> dict:
    return {
        "type": "limitation",
        "canonical_name": "PGIB limitation",
        "content": {"description": description},
        "source_provenance": {"start_char": start, "end_char": end, "batch_index": 0},
        "evidence_text": description,
        "confidence": confidence,
    }


def test_write_extraction_dedups_exact_duplicates(db_session: Session) -> None:
    ws = _workspace(db_session)
    paper, artifact = _paper_with_markdown(db_session, ws.id)
    run = _run(db_session, ws.id, paper, artifact)

    # 两个完全相同的方法项（范围和内容均相同）。
    counts = _write_extraction(
        db_session, paper, run, [_method_item(), _method_item()], []
    )
    db_session.commit()

    assert counts == (1, 0, 1, 0)  # 1 item, 0 relations, 1 span, 0 rejected relations
    assert db_session.execute(
        select(func.count()).select_from(KnowledgeItem)
    ).scalar_one() == 1

    # 被丢弃的重复项会作为拒绝记录，支持审计。
    rejection = db_session.execute(
        select(ExtractionRejection).where(
            ExtractionRejection.stage == "dedup_exact"
        )
    ).scalars().one_or_none()
    assert rejection is not None
    assert rejection.reason_code == "duplicate_item"
    assert rejection.item_type == "method"
    assert rejection.canonical_name == "GNNExplainer"


def test_write_extraction_dedups_same_span_claim_limitation(db_session: Session) -> None:
    ws = _workspace(db_session)
    paper, artifact = _paper_with_markdown(db_session, ws.id)
    run = _run(db_session, ws.id, paper, artifact)

    # 同一范围同时被分类为 claim 和 limitation。
    claim = _claim_item(statement="Position bias is present.", start=100, end=200, confidence=0.9)
    limitation = _limitation_item(description="Position bias is present.", start=100, end=200, confidence=0.6)

    counts = _write_extraction(db_session, paper, run, [claim, limitation], [])
    db_session.commit()

    # 保留置信度更高的 claim；limitation 被丢弃并记录拒绝。
    assert counts == (1, 0, 1, 0)
    items = db_session.execute(select(KnowledgeItem)).scalars().all()
    assert len(items) == 1
    assert items[0].type == "claim"

    rejection = db_session.execute(
        select(ExtractionRejection).where(ExtractionRejection.stage == "dedup_exact")
    ).scalars().one_or_none()
    assert rejection is not None
    assert rejection.item_type == "limitation"
