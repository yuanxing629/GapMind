from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from app.domains.artifact.models import Artifact
from app.domains.gap.context import (
    KNOWLEDGE_CONTEXT_MODE,
    LEGACY_CONTEXT_MODE,
    GapKnowledgeExtractionPendingError,
    build_gap_context,
    get_gap_context_identity,
)
from app.domains.gap.models import PaperGapAnnotation
from app.domains.gap.service import GapService
from app.domains.knowledge.models import EvidenceSpan, ExtractionRun, KnowledgeItem
from app.domains.paper.models import Paper
from app.domains.task.schemas import TaskCreate
from app.domains.task.service import TaskService
from app.domains.workspace.models import Workspace


def _paper_with_knowledge(db: Session) -> tuple[Workspace, Paper, Artifact, ExtractionRun]:
    workspace = Workspace(
        id=str(uuid4()),
        name="Knowledge context",
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    artifact = Artifact(
        id=str(uuid4()),
        workspace_id=workspace.id,
        kind="parsed_markdown",
        file_path="context.md",
        size_bytes=100,
        is_deleted=False,
    )
    paper = Paper(
        id=str(uuid4()),
        workspace_id=workspace.id,
        title="Context paper",
        authors=["Author"],
        source="manual",
        parse_status="parsed",
        parsed_markdown_artifact_id=artifact.id,
        chunk_count=1,
        extract_status="extracted",
        is_deleted=False,
    )
    db.add_all([workspace, artifact, paper])
    db.flush()
    task = TaskService(db).create(
        TaskCreate(
            workspace_id=workspace.id,
            task_type="extract_knowledge",
            payload={"paper_id": paper.id},
        )
    )
    now = datetime.now(UTC)
    run = ExtractionRun(
        id=str(uuid4()),
        workspace_id=workspace.id,
        paper_id=paper.id,
        artifact_id=artifact.id,
        task_id=task.id,
        schema_version="1.0.0",
        prompt_version="extract-v1",
        model_provider="deepseek",
        model_name="deepseek-test",
        status="succeeded",
        started_at=now,
        finished_at=now,
    )
    db.add(run)
    db.flush()
    method = KnowledgeItem(
        id=str(uuid4()),
        workspace_id=workspace.id,
        paper_id=paper.id,
        extraction_run_id=run.id,
        item_key="method:M1",
        type="method",
        canonical_name="结构化方法",
        content={"description": "针对目标问题的机制"},
        source_provenance={"batch_index": 0},
        created_by="system",
        confidence=0.95,
        status="extracted_candidate",
        is_deleted=False,
    )
    limitation = KnowledgeItem(
        id=str(uuid4()),
        workspace_id=workspace.id,
        paper_id=paper.id,
        extraction_run_id=run.id,
        item_key="limitation:L1",
        type="limitation",
        canonical_name="稳定性局限",
        content={"statement": "在分布外场景仍不稳定"},
        source_provenance={"batch_index": 0},
        created_by="system",
        confidence=0.9,
        status="extracted_candidate",
        is_deleted=False,
    )
    db.add_all([method, limitation])
    db.flush()
    db.add(
        EvidenceSpan(
            id=str(uuid4()),
            workspace_id=workspace.id,
            knowledge_item_id=limitation.id,
            paper_id=paper.id,
            artifact_id=artifact.id,
            artifact_kind="parsed_markdown",
            chunk_index=0,
            start_char=10,
            end_char=28,
            text="在分布外场景仍不稳定",
            relation="supports",
            confidence=0.9,
        )
    )
    db.add(
        EvidenceSpan(
            id=str(uuid4()),
            workspace_id=workspace.id,
            knowledge_item_id=method.id,
            paper_id=paper.id,
            artifact_id=artifact.id,
            artifact_kind="parsed_markdown",
            chunk_index=0,
            start_char=0,
            end_char=9,
            text="结构化方法",
            relation="supports",
            confidence=0.95,
        )
    )
    db.commit()
    return workspace, paper, artifact, run


def test_gap_context_uses_paper_local_knowledge_and_evidence(db_session: Session) -> None:
    _, paper, _, run = _paper_with_knowledge(db_session)

    context = build_gap_context(
        db_session,
        paper,
        "# Full paper\nThis unrelated full-text section must not become the default input.",
    )

    assert context.input_mode == KNOWLEDGE_CONTEXT_MODE
    assert context.knowledge_extraction_run_id == run.id
    assert context.knowledge_context_sha256
    assert context.knowledge_item_ids
    assert context.evidence_span_ids
    assert "结构化方法" in context.text
    assert "稳定性局限" in context.text
    assert "unrelated full-text" not in context.text
    assert (
        build_gap_context(db_session, paper, "different full text").knowledge_context_sha256
        == context.knowledge_context_sha256
    )


def test_gap_context_identity_falls_back_without_knowledge_run(db_session: Session) -> None:
    workspace = Workspace(
        id=str(uuid4()),
        name="Legacy context",
        keywords=[],
        active_questions=[],
        is_archived=False,
        is_deleted=False,
    )
    artifact = Artifact(
        id=str(uuid4()),
        workspace_id=workspace.id,
        kind="parsed_markdown",
        file_path="legacy.md",
        size_bytes=10,
        is_deleted=False,
    )
    paper = Paper(
        id=str(uuid4()),
        workspace_id=workspace.id,
        title="Legacy paper",
        authors=[],
        source="manual",
        parse_status="parsed",
        parsed_markdown_artifact_id=artifact.id,
        chunk_count=1,
        extract_status="not_applicable",
        is_deleted=False,
    )
    db_session.add_all([workspace, artifact, paper])
    db_session.commit()

    identity = get_gap_context_identity(db_session, paper)

    assert identity.input_mode == LEGACY_CONTEXT_MODE
    assert identity.knowledge_extraction_run_id is None
    assert identity.fallback_reason == "knowledge_extraction_unavailable"


def test_gap_worker_persists_knowledge_context_lineage(
    db_session: Session, monkeypatch, tmp_path
) -> None:
    from app.domains.artifact.service import ArtifactService
    from app.domains.gap.schemas import GapAnnotationOutput
    from app.workers.tasks.extract_gap_annotation import _run_gap_extraction

    workspace, paper, _, run = _paper_with_knowledge(db_session)
    markdown_path = tmp_path / "context.md"
    markdown_path.write_text("# Full paper\n" + "full text " * 100, encoding="utf-8")
    monkeypatch.setattr(ArtifactService, "resolve_abs_path", lambda self, item: markdown_path)

    task = TaskService(db_session).create(
        TaskCreate(
            workspace_id=workspace.id,
            task_type="extract_gap_annotation",
            payload={"paper_id": paper.id, "force": False},
        )
    )
    output = GapAnnotationOutput.model_validate(
        {
            "schema_version": "3.0",
            "paper": {"paper_name": "Context paper", "authors": [], "research_domain": []},
            "entities": [
                {
                    "entity_id": "E1",
                    "name_original": "method",
                    "name_normalized_zh": "结构化方法",
                    "type": "METHOD",
                    "description_zh": "method",
                },
                {
                    "entity_id": "E2",
                    "name_original": "problem",
                    "name_normalized_zh": "稳定性局限",
                    "type": "RESEARCH_PROBLEM",
                    "description_zh": "problem",
                },
            ],
            "relations": [
                {
                    "relation_id": "R1",
                    "source_entity_id": "E1",
                    "relation_type": "ADDRESSES",
                    "target_entity_id": "E2",
                }
            ],
            "methods": [
                {
                    "method_id": "M1",
                    "corresponding_entity_id": "E1",
                    "method_strategy_zh": "结构化方法",
                    "mechanism_zh": "机制",
                }
            ],
            "problems": [
                {
                    "problem_id": "P1",
                    "corresponding_entity_id": "E2",
                    "problem_label_zh": "稳定性局限",
                    "problem_type": "residual_limitation",
                    "description_zh": "分布外稳定性不足",
                }
            ],
        }
    )

    class FakeExtractor:
        model_parameters = {"provider": "test"}
        captured: str = ""

        def extract(self, text: str):
            self.captured = text
            from app.gateway.gap_extractor import GapExtractionResult

            return GapExtractionResult(
                output=output,
                attempts=1,
                validation_errors=[],
                provider="ollama",
                model="test-model",
                validation_error_categories=[],
            )

    fake = FakeExtractor()
    result = _run_gap_extraction(db_session, task.id, extractor=fake)
    annotation = db_session.get(PaperGapAnnotation, result["annotation_id"])

    assert result["status"] == "valid"
    assert annotation is not None
    assert annotation.input_mode == KNOWLEDGE_CONTEXT_MODE
    assert annotation.knowledge_extraction_run_id == run.id
    assert annotation.source_knowledge_item_ids
    assert annotation.source_evidence_span_ids
    assert annotation.context_fallback_reason is None
    assert "unrelated" not in fake.captured


def test_gap_context_excludes_deleted_knowledge_items(db_session: Session) -> None:
    _, paper, _, run = _paper_with_knowledge(db_session)
    deleted = KnowledgeItem(
        id=str(uuid4()),
        workspace_id=paper.workspace_id,
        paper_id=paper.id,
        extraction_run_id=run.id,
        item_key="limitation:deleted",
        type="limitation",
        canonical_name="已删除事实",
        content={"description": "must not be selected"},
        source_provenance={},
        created_by="system",
        confidence=1.0,
        status="extracted_candidate",
        is_deleted=True,
    )
    db_session.add(deleted)
    db_session.commit()

    context = build_gap_context(db_session, paper, "# Full paper")

    assert "已删除事实" not in context.text


def test_gap_submission_waits_for_running_knowledge_extraction(db_session: Session) -> None:
    from app.workers.tasks.extract_gap_annotation import spawn_gap_extraction

    workspace, paper, _, _ = _paper_with_knowledge(db_session)
    paper.extract_status = "extracting"
    db_session.commit()

    try:
        spawn_gap_extraction(db_session, paper.id, workspace.id)
    except GapKnowledgeExtractionPendingError as exc:
        assert "still running" in str(exc)
    else:
        raise AssertionError("gap extraction must wait for knowledge extraction")


def test_new_knowledge_run_marks_old_gap_annotation_stale(db_session: Session) -> None:
    workspace, paper, artifact, run = _paper_with_knowledge(db_session)
    annotation = PaperGapAnnotation(
        id=str(uuid4()),
        workspace_id=workspace.id,
        paper_id=paper.id,
        artifact_id=artifact.id,
        task_id=None,
        input_sha256="a" * 64,
        knowledge_extraction_run_id=run.id,
        knowledge_context_sha256="b" * 64,
        input_mode=KNOWLEDGE_CONTEXT_MODE,
        source_knowledge_item_ids=[],
        source_evidence_span_ids=[],
        context_char_count=10,
        context_fallback_reason=None,
        schema_version="3.0",
        prompt_version="gap-schema3-v3",
        model_provider="ollama",
        model_name="test-model",
        model_parameters={},
        status="valid",
        attempts=1,
        raw_responses=[],
        output=None,
        validation_errors=[],
        fallback_reason=None,
        is_deleted=False,
    )
    db_session.add(annotation)
    db_session.flush()
    assert GapService(db_session).annotation_is_stale(annotation) is False

    task = TaskService(db_session).create(
        TaskCreate(
            workspace_id=workspace.id,
            task_type="extract_knowledge",
            payload={"paper_id": paper.id},
        )
    )
    later = datetime.now(UTC) + timedelta(seconds=1)
    db_session.add(
        ExtractionRun(
            id=str(uuid4()),
            workspace_id=workspace.id,
            paper_id=paper.id,
            artifact_id=artifact.id,
            task_id=task.id,
            schema_version="1.0.0",
            prompt_version="extract-v2",
            model_provider="deepseek",
            model_name="deepseek-test",
            status="succeeded",
            started_at=later,
            finished_at=later,
        )
    )
    db_session.commit()

    assert GapService(db_session).annotation_is_stale(annotation) is True
