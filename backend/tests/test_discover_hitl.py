"""W5 HITL 决策测试：confirm / edit-confirm / reject / defer。

每个决策都通过 HTTP API 执行，然后同时在持久化的 HumanDecision 行和 Timeline event 上
验证（决策历史必须可追溯 - W5-1 + W5-3）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domains.artifact.models import Artifact
from app.domains.discover.models import (
    HumanDecision,
    OpportunityEvidence,
    OpportunityVersion,
    ResearchOpportunity,
)
from app.domains.knowledge.models import EvidenceSpan, KnowledgeItem
from app.domains.paper.models import Paper
from app.domains.timeline.models import TimelineEvent
from app.domains.workspace.models import Workspace


def _confirmable_opportunity(db_session: Session) -> tuple[dict, ResearchOpportunity, OpportunityVersion]:
    workspace_id = str(uuid4())
    workspace = Workspace(id=workspace_id, name="HITL workspace", is_archived=False)
    opportunity = ResearchOpportunity(
        id=str(uuid4()),
        workspace_id=workspace_id,
        title="Graph robustness under shift",
        summary="Summary",
        rationale="Rationale",
        suggested_directions=[],
        confidence=0.82,
        status="candidate",
        source_payload={"gate": {"missing": []}},
        is_deleted=False,
    )
    db_session.add_all([workspace, opportunity])
    db_session.flush()
    version = OpportunityVersion(
        id=str(uuid4()),
        opportunity_id=opportunity.id,
        version_number=1,
        title="Graph robustness under shift",
        problem_statement="Problem",
        research_scope="Scope",
        why_existing_work_is_insufficient="Why",
        candidate_research_question="RQ",
        candidate_hypothesis="Hypothesis",
        candidate_validation_plan={"steps": ["ablate"]},
        open_risks=[],
        novelty_score=0.7,
        feasibility_score=0.7,
        significance_score=0.7,
        confidence=0.82,
        evidence_coverage=1.0,
        verification_status="verified",
        synthesis_metadata={},
        created_by="agent",
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    db_session.flush()
    opportunity.current_version_id = version.id
    for index in range(2):
        paper_id = str(uuid4())
        artifact_id = str(uuid4())
        paper = Paper(id=paper_id, workspace_id=workspace_id, title=f"Paper {index}", authors=[], source="manual", is_deleted=False)
        artifact = Artifact(id=artifact_id, workspace_id=workspace_id, kind="parsed_markdown", file_path=f"paper-{index}.md", size_bytes=1, is_deleted=False)
        item = KnowledgeItem(id=str(uuid4()), workspace_id=workspace_id, paper_id=paper_id, type="claim", canonical_name="claim", content={}, source_provenance={}, created_by="agent", is_deleted=False)
        db_session.add_all([paper, artifact, item])
        db_session.flush()
        span = EvidenceSpan(id=str(uuid4()), workspace_id=workspace_id, knowledge_item_id=item.id, paper_id=paper_id, artifact_id=artifact_id, relation="supports", text="support", start_char=0, end_char=7, confidence=0.9)
        db_session.add(span)
        db_session.flush()
        db_session.add(OpportunityEvidence(id=str(uuid4()), opportunity_version_id=version.id, relation="supports", source_scope="workspace", evidence_level="full_text", paper_id=paper_id, evidence_span_id=span.id, artifact_id=artifact_id, chunk_id=f"chunk-{index}", judgement="supports", display_excerpt="support", snapshot_payload={}))
    db_session.commit()
    return {"id": workspace_id}, opportunity, version


def _decisions(db_session: Session, opportunity_id: str) -> list[HumanDecision]:
    return list(
        db_session.execute(
            select(HumanDecision)
            .where(HumanDecision.opportunity_id == opportunity_id)
            .order_by(HumanDecision.created_at)
        ).scalars()
    )


def _timeline_events(db_session: Session, workspace_id: str, subject_id: str) -> list[TimelineEvent]:
    return list(
        db_session.execute(
            select(TimelineEvent)
            .where(TimelineEvent.workspace_id == workspace_id, TimelineEvent.subject_id == subject_id)
            .order_by(TimelineEvent.created_at)
        ).scalars()
    )


def test_confirm_decision_persists_and_emits_timeline(client, db_session: Session):
    workspace, opportunity, version = _confirmable_opportunity(db_session)
    wid = workspace["id"]

    resp = client.post(
        f"/api/v1/workspaces/{wid}/discover/opportunities/{opportunity.id}/confirm",
        json={"version_id": version.id, "note": "同意"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"

    decisions = _decisions(db_session, opportunity.id)
    assert len(decisions) == 1
    assert decisions[0].action == "confirm"
    assert decisions[0].reason == "同意"
    assert decisions[0].actor == "user"
    events = _timeline_events(db_session, wid, opportunity.id)
    assert any(e.event_type == "opportunity.confirmed" for e in events)


def test_edit_confirm_creates_new_version(client, db_session: Session):
    workspace, opportunity, version = _confirmable_opportunity(db_session)
    wid = workspace["id"]

    resp = client.patch(
        f"/api/v1/workspaces/{wid}/discover/opportunities/{opportunity.id}",
        json={"base_version_id": version.id, "changes": {"title": "Tighter scope"}, "note": "收紧范围"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "edited_confirmed"

    decisions = _decisions(db_session, opportunity.id)
    assert decisions[0].action == "edit_confirm"
    versions = list(
        db_session.execute(
            select(OpportunityVersion).where(OpportunityVersion.opportunity_id == opportunity.id)
        ).scalars()
    )
    assert len(versions) == 2  # base + user-edited
    assert body["id"] == opportunity.id
    events = _timeline_events(db_session, wid, opportunity.id)
    assert any(e.event_type == "opportunity.edited_confirmed" for e in events)


def test_reject_decision_persists_and_emits_timeline(client, db_session: Session):
    workspace, opportunity, _ = _confirmable_opportunity(db_session)
    wid = workspace["id"]

    resp = client.post(
        f"/api/v1/workspaces/{wid}/discover/opportunities/{opportunity.id}/reject",
        json={"note": "已有更强工作覆盖"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"

    decisions = _decisions(db_session, opportunity.id)
    assert decisions[0].action == "reject"
    assert decisions[0].reason == "已有更强工作覆盖"
    events = _timeline_events(db_session, wid, opportunity.id)
    assert any(e.event_type == "opportunity.rejected" for e in events)


def test_defer_decision_keeps_condition(client, db_session: Session):
    workspace, opportunity, _ = _confirmable_opportunity(db_session)
    wid = workspace["id"]

    resp = client.post(
        f"/api/v1/workspaces/{wid}/discover/opportunities/{opportunity.id}/defer",
        json={"note": "等更多反证", "defer_condition": "补 3 篇外部全文后重审"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "deferred"

    decisions = _decisions(db_session, opportunity.id)
    assert decisions[0].action == "defer"
    assert decisions[0].defer_condition == "补 3 篇外部全文后重审"
    events = _timeline_events(db_session, wid, opportunity.id)
    assert any(e.event_type == "opportunity.deferred" for e in events)


def test_decision_history_is_traceable(client, db_session: Session):
    workspace, opportunity, _ = _confirmable_opportunity(db_session)
    wid = workspace["id"]

    client.post(f"/api/v1/workspaces/{wid}/discover/opportunities/{opportunity.id}/defer", json={"defer_condition": "等数据"})
# Deferred opportunity 之后仍可确认（不是终态）。
    resp = client.post(f"/api/v1/workspaces/{wid}/discover/opportunities/{opportunity.id}/confirm", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"

    decisions = _decisions(db_session, opportunity.id)
    actions = [d.action for d in decisions]
    assert actions == ["defer", "confirm"]  # chronological history preserved
    assert len(decisions[0].id) == 36
