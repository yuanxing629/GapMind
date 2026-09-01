from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

import pytest

from app.domains.artifact.models import Artifact
from app.domains.discover.models import DiscoverExternalCandidate, DiscoverRun
from app.domains.discover.models import OpportunityEvidence, OpportunityVersion, ResearchOpportunity
from app.domains.discover.exceptions import DiscoverGateError
from app.domains.discover.service import (
    DiscoverRunCancelled,
    DiscoverService,
    resume_discover_runs_for_paper,
)
from app.domains.knowledge.models import EvidenceSpan, KnowledgeItem
from app.domains.paper.models import Paper
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem
from app.domains.task.models import Task
from app.domains.workspace.models import Workspace


def _supporting_item(
    paper_id: str, artifact_id: str, text: str, chunk_id: str
) -> RetrievalResultItem:
    return RetrievalResultItem(
        paper_id=paper_id,
        paper_title="Paper",
        artifact_id=artifact_id,
        chunk_id=chunk_id,
        text=text,
        evidence_level="full_text",
        judgement="supports",
        source_scope="workspace",
    )


def _run(workspace_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(uuid4()),
        workspace_id=workspace_id,
        stage_summaries={"external_search": {"status": "succeeded", "executed": True}},
    )


def _supporting_response(items: list[RetrievalResultItem]) -> RetrievalResponse:
    return RetrievalResponse(
        workspace_id="workspace",
        purpose="supporting_evidence",
        status="succeeded",
        items=items,
        total=len(items),
    )


def _candidate() -> dict:
    return {
        "problem_statement": "robust graph learning behavior under shift",
        "candidate_hypothesis": "robust graph learning improves under shift",
        "why_existing_work_is_insufficient": "existing graph learning evidence is limited",
    }


def test_similar_work_cannot_count_as_supporting_evidence(db_session) -> None:
    workspace_id = str(uuid4())
    service = DiscoverService(db_session)
    similar = _supporting_response(
        [
            _supporting_item(
                str(uuid4()), str(uuid4()), "robust graph learning behavior under shift", "s1"
            ),
            _supporting_item(
                str(uuid4()), str(uuid4()), "robust graph learning improves under shift", "s2"
            ),
        ]
    )
    counter = RetrievalResponse(
        workspace_id=workspace_id, purpose="counter_evidence", status="succeeded"
    )
    gate = service._evidence_gate(
        _run(workspace_id),
        candidate=_candidate(),
        supporting=_supporting_response([]),
        counter=counter,
    )
    assert gate["verified"] is False
    assert gate["independent_full_text_papers"] == 0


def test_metadata_only_and_duplicate_chunks_do_not_pass_gate(db_session) -> None:
    workspace_id = str(uuid4())
    paper_id = str(uuid4())
    artifact_id = str(uuid4())
    workspace = Workspace(id=workspace_id, name="Gate workspace", is_archived=False)
    paper = Paper(
        id=paper_id,
        workspace_id=workspace_id,
        title="Paper",
        authors=[],
        source="manual",
        is_deleted=False,
    )
    artifact = Artifact(
        id=artifact_id,
        workspace_id=workspace_id,
        kind="parsed_markdown",
        file_path="missing.md",
        size_bytes=0,
        is_deleted=False,
    )
    item = KnowledgeItem(
        id=str(uuid4()),
        workspace_id=workspace_id,
        paper_id=paper_id,
        type="claim",
        canonical_name="claim",
        content={},
        source_provenance={},
        created_by="agent",
        is_deleted=False,
    )
    db_session.add_all([workspace, paper, artifact, item])
    db_session.flush()
    db_session.add(
        EvidenceSpan(
            id=str(uuid4()),
            workspace_id=workspace_id,
            knowledge_item_id=item.id,
            paper_id=paper_id,
            artifact_id=artifact_id,
            relation="supports",
            text="robust graph learning behavior under shift",
            start_char=0,
            end_char=44,
            confidence=0.9,
        )
    )
    db_session.commit()
    service = DiscoverService(db_session)
    duplicate = _supporting_item(
        paper_id, artifact_id, "robust graph learning behavior under shift", "c1"
    )
    duplicate2 = _supporting_item(
        paper_id, artifact_id, "robust graph learning improves under shift", "c2"
    )
    metadata = _supporting_item(str(uuid4()), str(uuid4()), "robust graph learning evidence", "m1")
    metadata.evidence_level = "metadata_only"
    counter = RetrievalResponse(
        workspace_id=workspace_id, purpose="counter_evidence", status="succeeded"
    )
    gate = service._evidence_gate(
        _run(workspace_id),
        candidate=_candidate(),
        supporting=_supporting_response([duplicate, duplicate2, metadata]),
        counter=counter,
    )
    assert gate["verified"] is False
    assert gate["independent_full_text_papers"] == 1


def test_two_span_backed_supports_papers_pass_gate(db_session) -> None:
    workspace_id = str(uuid4())
    workspace = Workspace(id=workspace_id, name="Gate workspace", is_archived=False)
    items = []
    retrieval = []
    for index in range(2):
        paper_id = str(uuid4())
        artifact_id = str(uuid4())
        paper = Paper(
            id=paper_id,
            workspace_id=workspace_id,
            title=f"Paper {index}",
            authors=[],
            source="manual",
            is_deleted=False,
        )
        artifact = Artifact(
            id=artifact_id,
            workspace_id=workspace_id,
            kind="parsed_markdown",
            file_path=f"paper-{index}.md",
            size_bytes=1,
            is_deleted=False,
        )
        item = KnowledgeItem(
            id=str(uuid4()),
            workspace_id=workspace_id,
            paper_id=paper_id,
            type="claim",
            canonical_name="claim",
            content={},
            source_provenance={},
            created_by="agent",
            is_deleted=False,
        )
        items.append((paper, artifact, item))
        retrieval.append(
            _supporting_item(
                paper_id,
                artifact_id,
                "robust graph learning behavior under shift",
                f"chunk-{index}",
            )
        )
    db_session.add(workspace)
    db_session.flush()
    for paper, artifact, item in items:
        db_session.add_all([paper, artifact, item])
        db_session.flush()
        db_session.add(
            EvidenceSpan(
                id=str(uuid4()),
                workspace_id=workspace_id,
                knowledge_item_id=item.id,
                paper_id=paper.id,
                artifact_id=artifact.id,
                relation="supports",
                text="robust graph learning behavior under shift",
                start_char=0,
                end_char=44,
                confidence=0.9,
            )
        )
    db_session.commit()
    service = DiscoverService(db_session)
    counter = RetrievalResponse(
        workspace_id=workspace_id, purpose="counter_evidence", status="succeeded"
    )
    gate = service._evidence_gate(
        _run(workspace_id),
        candidate=_candidate(),
        supporting=_supporting_response(retrieval),
        counter=counter,
    )
    assert gate["verified"] is True
    assert gate["confirmable"] is True
    assert gate["independent_full_text_papers"] == 2
    assert gate["evidence_coverage"] >= 0.6


def test_cross_language_candidate_support_is_not_removed_by_lexical_filter(db_session) -> None:
    workspace_id = str(uuid4())
    workspace = Workspace(id=workspace_id, name="Cross-language gate", is_archived=False)
    db_session.add(workspace)
    db_session.flush()
    retrieval = []
    for index, text in enumerate(
        [
            "Information bottleneck feature selection preserves diverse predictive factors.",
            "Counterfactual coverage improves when independent plausible factors are retained.",
        ]
    ):
        paper_id = str(uuid4())
        artifact_id = str(uuid4())
        paper = Paper(
            id=paper_id,
            workspace_id=workspace_id,
            title=f"English Paper {index}",
            authors=[],
            source="manual",
            is_deleted=False,
        )
        artifact = Artifact(
            id=artifact_id,
            workspace_id=workspace_id,
            kind="parsed_markdown",
            file_path=f"english-{index}.md",
            size_bytes=1,
            is_deleted=False,
        )
        item = KnowledgeItem(
            id=str(uuid4()),
            workspace_id=workspace_id,
            paper_id=paper_id,
            type="claim",
            canonical_name="claim",
            content={},
            source_provenance={},
            created_by="agent",
            is_deleted=False,
        )
        db_session.add_all([paper, artifact, item])
        db_session.flush()
        db_session.add(
            EvidenceSpan(
                id=str(uuid4()),
                workspace_id=workspace_id,
                knowledge_item_id=item.id,
                paper_id=paper_id,
                artifact_id=artifact_id,
                relation="supports",
                text=text,
                start_char=0,
                end_char=len(text),
                confidence=0.9,
            )
        )
        retrieval.append(_supporting_item(paper_id, artifact_id, text, f"cross-{index}"))
    db_session.commit()

    candidate = {
        "problem_statement": "反事实解释覆盖不足",
        "candidate_hypothesis": "信息瓶颈保留多样信息特征可以提升反事实覆盖度",
        "why_existing_work_is_insufficient": "现有最小编辑方法覆盖的可行替代空间有限",
    }
    gate = DiscoverService(db_session)._evidence_gate(
        _run(workspace_id),
        candidate=candidate,
        supporting=_supporting_response(retrieval),
        counter=RetrievalResponse(
            workspace_id=workspace_id,
            purpose="counter_evidence",
            status="succeeded",
        ),
    )

    assert gate["confirmable"] is True
    assert gate["independent_full_text_papers"] == 2
    assert gate["evidence_coverage"] == 0.933


def test_external_verification_is_warning_when_core_evidence_passes(db_session) -> None:
    workspace_id = str(uuid4())
    workspace = Workspace(id=workspace_id, name="Warning workspace", is_archived=False)
    db_session.add(workspace)
    db_session.flush()
    retrieval = []
    for index in range(2):
        paper_id = str(uuid4())
        artifact_id = str(uuid4())
        paper = Paper(
            id=paper_id,
            workspace_id=workspace_id,
            title=f"Paper {index}",
            authors=[],
            source="manual",
            is_deleted=False,
        )
        artifact = Artifact(
            id=artifact_id,
            workspace_id=workspace_id,
            kind="parsed_markdown",
            file_path=f"paper-{index}.md",
            size_bytes=1,
            is_deleted=False,
        )
        item = KnowledgeItem(
            id=str(uuid4()),
            workspace_id=workspace_id,
            paper_id=paper_id,
            type="claim",
            canonical_name="claim",
            content={},
            source_provenance={},
            created_by="agent",
            is_deleted=False,
        )
        db_session.add_all([paper, artifact, item])
        db_session.flush()
        db_session.add(
            EvidenceSpan(
                id=str(uuid4()),
                workspace_id=workspace_id,
                knowledge_item_id=item.id,
                paper_id=paper_id,
                artifact_id=artifact_id,
                relation="supports",
                text="robust graph learning behavior under shift",
                start_char=0,
                end_char=44,
                confidence=0.9,
            )
        )
        retrieval.append(
            _supporting_item(
                paper_id,
                artifact_id,
                "robust graph learning behavior under shift",
                f"chunk-{index}",
            )
        )
    db_session.commit()
    run = _run(workspace_id)
    run.stage_summaries = {"external_search": {"external_candidates": 2}}
    counter = RetrievalResponse(
        workspace_id=workspace_id, purpose="counter_evidence", status="succeeded"
    )

    gate = DiscoverService(db_session)._evidence_gate(
        run,
        candidate=_candidate(),
        supporting=_supporting_response(retrieval),
        counter=counter,
    )

    assert gate["verified"] is False
    assert gate["confirmable"] is True
    assert gate["blocking_missing"] == []
    assert gate["warnings"] == ["external verification did not complete"]


def test_skipped_external_selection_is_a_non_blocking_verification_warning(db_session) -> None:
    run = _run(str(uuid4()))
    run.stage_summaries = {
        "external_search": {"status": "succeeded", "external_candidates": 3},
        "external_selection": {"status": "skipped", "reason": "user_skipped"},
    }
    service = DiscoverService(db_session)

    assert service._external_selection_skipped(run) is True
    gate = service._evidence_gate(
        run,
        candidate=None,
        supporting=_supporting_response([]),
        counter=RetrievalResponse(
            workspace_id=run.workspace_id,
            purpose="counter_evidence",
            status="succeeded",
        ),
    )
    assert gate["external_search_executed"] is True
    assert gate["external_verification_completed"] is False
    assert "external verification did not complete" in gate["warnings"]


def test_partial_external_search_counts_as_executed(db_session) -> None:
    run = _run(str(uuid4()))
    run.stage_summaries = {
        "external_search": {
            "status": "succeeded_partial",
            "successful_query_count": 2,
            "failed_query_count": 1,
        }
    }
    gate = DiscoverService(db_session)._evidence_gate(
        run,
        candidate=None,
        supporting=_supporting_response([]),
        counter=RetrievalResponse(
            workspace_id=run.workspace_id,
            purpose="counter_evidence",
            status="succeeded",
        ),
    )

    assert gate["external_search_executed"] is True
    assert gate["external_search_complete"] is False
    assert gate["external_verification_completed"] is False
    assert gate["external_search_status"] == "succeeded_partial"
    assert "external verification did not complete" in gate["warnings"]


def test_exact_lookup_failure_is_also_incomplete_external_coverage(db_session) -> None:
    run = _run(str(uuid4()))
    run.stage_summaries = {
        "external_search": {
            "status": "succeeded",
            "successful_query_count": 8,
            "failed_query_count": 0,
            "exact_lookup_failure_count": 1,
        }
    }
    gate = DiscoverService(db_session)._evidence_gate(
        run,
        candidate=None,
        supporting=_supporting_response([]),
        counter=RetrievalResponse(
            workspace_id=run.workspace_id,
            purpose="counter_evidence",
            status="succeeded",
        ),
    )

    assert gate["external_search_executed"] is True
    assert gate["external_search_complete"] is False
    assert gate["external_verification_completed"] is False
    assert "external verification did not complete" in gate["warnings"]


def test_incomplete_gate_does_not_cap_agent_confidence() -> None:
    candidate = DiscoverService._normalize_candidate(
        {"confidence": 0.82},
        {
            "verified": False,
            "confirmable": False,
            "evidence_coverage": 0.25,
            "independent_full_text_papers": 1,
        },
        provider="test",
    )

    assert candidate["confidence"] == 0.82
    assert candidate["evidence_coverage"] == 0.25


def test_external_warning_allows_human_confirmation_but_core_failure_does_not(db_session) -> None:
    workspace_id = str(uuid4())
    workspace = Workspace(id=workspace_id, name="Human review workspace", is_archived=False)
    opportunity = ResearchOpportunity(
        id=str(uuid4()),
        workspace_id=workspace_id,
        title="Opportunity",
        summary="Summary",
        rationale="Rationale",
        suggested_directions=[],
        confidence=0.82,
        status="needs_more_evidence",
        source_payload={"gate": {"missing": ["external verification did not complete"]}},
        is_deleted=False,
    )
    db_session.add_all([workspace, opportunity])
    db_session.flush()
    version = OpportunityVersion(
        id=str(uuid4()),
        opportunity_id=opportunity.id,
        version_number=1,
        title="Opportunity",
        problem_statement="Problem",
        evidence_coverage=1.0,
        confidence=0.82,
        verification_status="verification_incomplete",
        created_by="agent",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    db_session.flush()
    opportunity.current_version_id = version.id
    for index in range(2):
        paper_id = str(uuid4())
        artifact_id = str(uuid4())
        paper = Paper(
            id=paper_id,
            workspace_id=workspace_id,
            title=f"Paper {index}",
            authors=[],
            source="manual",
            is_deleted=False,
        )
        artifact = Artifact(
            id=artifact_id,
            workspace_id=workspace_id,
            kind="parsed_markdown",
            file_path=f"paper-{index}.md",
            size_bytes=1,
            is_deleted=False,
        )
        item = KnowledgeItem(
            id=str(uuid4()),
            workspace_id=workspace_id,
            paper_id=paper_id,
            type="claim",
            canonical_name="claim",
            content={},
            source_provenance={},
            created_by="agent",
            is_deleted=False,
        )
        db_session.add_all([paper, artifact, item])
        db_session.flush()
        span = EvidenceSpan(
            id=str(uuid4()),
            workspace_id=workspace_id,
            knowledge_item_id=item.id,
            paper_id=paper_id,
            artifact_id=artifact_id,
            relation="supports",
            text="support",
            start_char=0,
            end_char=7,
            confidence=0.9,
        )
        db_session.add(span)
        db_session.flush()
        db_session.add(
            OpportunityEvidence(
                id=str(uuid4()),
                opportunity_version_id=version.id,
                relation="supports",
                source_scope="workspace",
                evidence_level="full_text",
                paper_id=paper_id,
                evidence_span_id=span.id,
                artifact_id=artifact_id,
                chunk_id=f"chunk-{index}",
                judgement="supports",
                display_excerpt="support",
                snapshot_payload={},
            )
        )
    db_session.commit()

    workflow = DiscoverService(db_session)
    workflow._require_confirmable(opportunity, version)

    opportunity.source_payload = {"gate": {"missing": ["counter evidence status is degraded"]}}
    with pytest.raises(DiscoverGateError):
        workflow._require_confirmable(opportunity, version)


def test_candidate_relevance_does_not_fall_back_to_broad_topic_results(db_session) -> None:
    service = DiscoverService(db_session)
    items = [
        _supporting_item(str(uuid4()), str(uuid4()), "unrelated optimization benchmark", "c1"),
    ]
    candidate = {
        "problem_statement": "graph shift robustness",
        "candidate_hypothesis": "robustness improves under distribution shift",
        "why_existing_work_is_insufficient": "the boundary condition is unknown",
    }
    assert service._supporting_for_candidate(candidate, items) == []


def test_counter_evidence_outcomes_remain_distinguishable(db_session) -> None:
    service = DiscoverService(db_session)
    empty = RetrievalResponse(workspace_id="w", purpose="counter_evidence", status="succeeded")
    degraded = RetrievalResponse(workspace_id="w", purpose="counter_evidence", status="degraded")
    failed = RetrievalResponse(workspace_id="w", purpose="counter_evidence", status="failed")
    assert service._counter_summary(empty)["outcome"] == "searched_no_counter_evidence"
    assert service._counter_summary(degraded)["outcome"] == "judge_degraded_or_failed"
    assert service._counter_summary(failed)["outcome"] == "retrieval_failed"


def test_degraded_counter_evidence_cannot_pass_final_gate(db_session) -> None:
    workspace_id = str(uuid4())
    workspace = Workspace(id=workspace_id, name="Gate workspace", is_archived=False)
    db_session.add(workspace)
    db_session.flush()
    retrieval = []
    for index in range(2):
        paper_id = str(uuid4())
        artifact_id = str(uuid4())
        paper = Paper(
            id=paper_id,
            workspace_id=workspace_id,
            title=f"Paper {index}",
            authors=[],
            source="manual",
            is_deleted=False,
        )
        artifact = Artifact(
            id=artifact_id,
            workspace_id=workspace_id,
            kind="parsed_markdown",
            file_path=f"paper-{index}.md",
            size_bytes=1,
            is_deleted=False,
        )
        item = KnowledgeItem(
            id=str(uuid4()),
            workspace_id=workspace_id,
            paper_id=paper_id,
            type="claim",
            canonical_name="claim",
            content={},
            source_provenance={},
            created_by="agent",
            is_deleted=False,
        )
        db_session.add_all([paper, artifact, item])
        db_session.flush()
        db_session.add(
            EvidenceSpan(
                id=str(uuid4()),
                workspace_id=workspace_id,
                knowledge_item_id=item.id,
                paper_id=paper_id,
                artifact_id=artifact_id,
                relation="supports",
                text="robust graph learning behavior under shift",
                start_char=0,
                end_char=44,
                confidence=0.9,
            )
        )
        retrieval.append(
            _supporting_item(
                paper_id,
                artifact_id,
                "robust graph learning behavior under shift",
                f"chunk-{index}",
            )
        )
    db_session.commit()
    service = DiscoverService(db_session)
    counter = RetrievalResponse(
        workspace_id=workspace_id, purpose="counter_evidence", status="degraded"
    )
    gate = service._evidence_gate(
        _run(workspace_id),
        candidate=_candidate(),
        supporting=_supporting_response(retrieval),
        counter=counter,
    )
    assert gate["verified"] is False
    assert "counter evidence status is degraded" in gate["missing"]


def test_stage_stops_when_run_was_cancelled(db_session) -> None:
    workspace_id = str(uuid4())
    workspace = Workspace(id=workspace_id, name="Cancelled workspace", is_archived=False)
    run = DiscoverRun(
        id=str(uuid4()),
        workspace_id=workspace_id,
        input_topic="topic",
        input_payload={},
        scope={},
        config={},
        status="cancelled",
        stage="cancelled",
        progress=0.4,
        verification_status="incomplete",
        stage_summaries={},
    )
    db_session.add_all([workspace, run])
    db_session.commit()
    with pytest.raises(DiscoverRunCancelled):
        DiscoverService(db_session)._stage(run, "synthesis", 0.8)


def test_fulltext_pipeline_resumes_when_at_least_one_batch_candidate_is_verified(
    db_session,
) -> None:
    workspace_id = str(uuid4())
    paper_id = str(uuid4())
    artifact_id = str(uuid4())
    run_id = str(uuid4())
    task_id = str(uuid4())
    workspace = Workspace(id=workspace_id, name="Resume workspace", is_archived=False)
    paper = Paper(
        id=paper_id,
        workspace_id=workspace_id,
        title="Imported",
        authors=[],
        source="semantic_scholar",
        parse_status="parsed",
        parsed_markdown_artifact_id=artifact_id,
        parsed_text_artifact_id=str(uuid4()),
        extract_status="extracted",
        is_deleted=False,
    )
    artifact = Artifact(
        id=artifact_id,
        workspace_id=workspace_id,
        kind="parsed_markdown",
        file_path="paper.md",
        size_bytes=1,
        is_deleted=False,
    )
    item = KnowledgeItem(
        id=str(uuid4()),
        workspace_id=workspace_id,
        paper_id=paper_id,
        type="claim",
        canonical_name="claim",
        content={},
        source_provenance={},
        created_by="agent",
        is_deleted=False,
    )
    db_session.add_all([workspace, paper, artifact, item])
    db_session.flush()
    db_session.add(
        EvidenceSpan(
            id=str(uuid4()),
            workspace_id=workspace_id,
            knowledge_item_id=item.id,
            paper_id=paper_id,
            artifact_id=artifact_id,
            relation="supports",
            text="supporting evidence",
            start_char=0,
            end_char=19,
            confidence=0.9,
        )
    )
    task = Task(
        id=task_id,
        workspace_id=workspace_id,
        task_type="discover_agent",
        status="waiting_for_user",
        progress=0.68,
        payload={"run_id": run_id},
        is_deleted=False,
    )
    run = DiscoverRun(
        id=run_id,
        workspace_id=workspace_id,
        task_id=task_id,
        input_topic="topic",
        input_payload={},
        scope={},
        config={},
        status="waiting_for_fulltext",
        stage="fulltext_verification",
        progress=0.68,
        verification_status="in_progress",
        stage_summaries={"external_search": {"status": "succeeded"}},
    )
    candidate = DiscoverExternalCandidate(
        id=str(uuid4()),
        discover_run_id=run_id,
        query="topic",
        rank=1,
        external_paper_id="S2-1",
        title="Imported",
        authors=[],
        evidence_level="metadata_only",
        verification_status="imported_pending_parse",
        imported_paper_id=paper_id,
        snapshot_payload={},
    )
    failed_candidate = DiscoverExternalCandidate(
        id=str(uuid4()),
        discover_run_id=run_id,
        query="topic",
        rank=2,
        external_paper_id="S2-2",
        title="No open PDF",
        authors=[],
        evidence_level="metadata_only",
        verification_status="no_pdf",
        snapshot_payload={},
    )
    embed_task = Task(
        id=str(uuid4()),
        workspace_id=workspace_id,
        task_type="embed_chunks",
        status="succeeded",
        progress=1.0,
        payload={"paper_id": paper_id},
        result={"indexed_count": 3},
        is_deleted=False,
    )
    db_session.add_all([task, run, candidate, failed_candidate, embed_task])
    db_session.commit()

    with patch(
        "app.workers.tasks.run_discover.spawn_discover_task", return_value="resumed-celery-id"
    ):
        resume_discover_runs_for_paper(db_session, paper_id, workspace_id)

    db_session.refresh(run)
    db_session.refresh(candidate)
    assert run.status == "queued"
    assert candidate.verification_status == "verified"
    assert failed_candidate.verification_status == "no_pdf"
    assert run.stage_summaries["fulltext_verification"]["verified"] == 1
    assert run.stage_summaries["fulltext_verification"]["failed"] == 1
    assert db_session.get(Task, task_id).status == "running"
    assert db_session.get(Task, task_id).celery_task_id == "resumed-celery-id"
