"""Tests for the Discover multi-agent orchestration (MA workstream).

Covers the AgentRun/AgentStep observability layer and the CriticAgent:
find-or-create AgentRun keyed by the Discover task, monotonic AgentStep
recording, critic review parsing/fallback, and verdict down-weighting.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.domains.agent.models import AgentRun, AgentStep  # noqa: E402
from app.domains.discover.models import (  # noqa: E402
    DiscoverRun,
    OpportunityEvidence,
    OpportunityVersion,
    ResearchOpportunity,
)
from app.domains.discover.service import DiscoverService  # noqa: E402
from app.domains.knowledge.models import EvidenceSpan  # noqa: E402
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem  # noqa: E402
from app.domains.task.models import Task  # noqa: E402
from app.domains.workspace.models import Workspace  # noqa: E402


def _run(workspace_id: str, *, task_id: str | None = None, **overrides: Any) -> DiscoverRun:
    kwargs = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "task_id": task_id,
        "trigger_type": "topic",
        "input_topic": "GNN interpretability under distribution shift",
        "input_payload": {"topic": "GNN interpretability under distribution shift", "keywords": []},
        "scope": {},
        "config": {"top_k": 10},
        "status": "running",
        "stage": "preflight",
        "progress": 0.05,
        "verification_status": "in_progress",
        "stage_summaries": {},
    }
    kwargs.update(overrides)
    return DiscoverRun(**kwargs)


class _S2Fake:
    def __init__(self, per_query: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.per_query = per_query or {}

    def search(self, query: str, *, fields: str, **kw: Any):
        return {"data": self.per_query.get(query, []), "total": len(self.per_query.get(query, []))}

    def get_paper(self, paper_id: str, *, fields: str):
        return {"paperId": paper_id}


class _NoopLLM:
    def chat_completion(self, messages, **kwargs):
        return SimpleNamespace(content=json.dumps({"roles": []}))


class _CriticLLM:
    """Returns critic reviews; records the user prompt for assertions."""

    def __init__(self, reviews: list[dict[str, Any]]) -> None:
        self.reviews = reviews
        self.messages: list[list[dict[str, str]]] = []

    def chat_completion(self, messages, **kwargs):
        self.messages.append(messages)
        return SimpleNamespace(content=json.dumps({"reviews": self.reviews}))


class _BoomLLM:
    def chat_completion(self, messages, **kwargs):
        raise RuntimeError("llm down")


class _FakeRetrieval:
    """Retrieval port fake whose counter-evidence search returns canned items."""

    def __init__(self, counter_items: list[RetrievalResultItem] | None = None) -> None:
        self.counter_items = counter_items or []
        self.counter_calls: list[dict[str, Any]] = []

    def semantic_search(self, workspace_id: str, query: str, top_k: int, **kw: Any):
        return _empty_response(workspace_id, "supporting")

    def find_similar_work(self, workspace_id: str, paper_id: str, top_k: int, **kw: Any):
        return _empty_response(workspace_id, "similar_work")

    def find_counter_evidence(self, workspace_id: str, claim: str, top_k: int, **kw: Any):
        self.counter_calls.append({"claim": claim, "top_k": top_k})
        return RetrievalResponse(workspace_id=workspace_id, purpose="counter_evidence", status="succeeded", items=self.counter_items, total=len(self.counter_items))


def _counter_item(judgement: str, confidence: float) -> RetrievalResultItem:
    return RetrievalResultItem(
        paper_id=str(uuid4()),
        paper_title="Counter Paper",
        artifact_id=str(uuid4()),
        chunk_id="c1",
        text="existing work already covers the narrowed claim",
        evidence_level="full_text",
        judgement=judgement,
        judgement_confidence=confidence,
        source_scope="workspace",
    )


def _service(db_session, llm=None) -> DiscoverService:
    return DiscoverService(db_session, external_search=_S2Fake({}), llm=llm or _NoopLLM())


def _empty_response(workspace_id: str, purpose: str = "evidence") -> RetrievalResponse:
    return RetrievalResponse(workspace_id=workspace_id, purpose=purpose, status="succeeded", items=[])


def _workspace(db_session, workspace_id: str) -> None:
    db_session.add(Workspace(id=workspace_id, name="MA test workspace", is_archived=False))
    db_session.commit()


# ------------------------------------------------------------------ AgentRun / AgentStep
def test_discover_agent_run_find_or_create_is_idempotent(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    task = Task(id=str(uuid4()), workspace_id=workspace_id, task_type="discover_agent", status="queued", payload={})
    run = _run(workspace_id, task_id=task.id)
    db_session.add_all([task, run])
    db_session.commit()

    service = _service(db_session)
    first = service._discover_agent_run(run)
    second = service._discover_agent_run(run)
    assert first.id == second.id  # keyed by task_id → no duplicate on resume
    assert first.agent_type == "discover"
    assert first.task_id == task.id
    assert (first.input_payload or {}).get("discover_run_id") == run.id


def test_discover_agent_run_without_task_creates_new(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    run = _run(workspace_id)  # no task_id
    db_session.add(run)
    db_session.commit()
    service = _service(db_session)
    assert service._discover_agent_run(run).id != service._discover_agent_run(run).id


def test_agent_step_appends_monotonic_sequence(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    agent_run = AgentRun(workspace_id=workspace_id, agent_type="discover", status="running", current_stage="planner")
    db_session.add(agent_run)
    db_session.commit()

    service = _service(db_session)
    service._agent_step(agent_run, "planner", "completed", "Planned")
    service._agent_step(agent_run, "evidence", "completed", "Retrieved", {"n": 3})

    steps = list(db_session.query(AgentStep).filter(AgentStep.run_id == agent_run.id).order_by(AgentStep.sequence).all())
    assert [s.sequence for s in steps] == [1, 2]
    assert [s.stage for s in steps] == ["planner", "evidence"]
    assert steps[1].details == {"n": 3}
    assert agent_run.current_stage == "evidence"


def test_agent_step_does_not_overwrite_terminal_status(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    agent_run = AgentRun(workspace_id=workspace_id, agent_type="discover", status="succeeded", current_stage="gate")
    db_session.add(agent_run)
    db_session.commit()
    service = _service(db_session)
    service._agent_step(agent_run, "complete", "completed", "Finished")
    assert agent_run.status == "succeeded"  # terminal status preserved
    assert agent_run.current_stage == "complete"


def test_run_agent_steps_returns_handoff_for_task(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    task = Task(id=str(uuid4()), workspace_id=workspace_id, task_type="discover_agent", status="succeeded", payload={})
    run = _run(workspace_id, task_id=task.id)
    db_session.add_all([task, run])
    db_session.commit()
    service = _service(db_session)
    assert service._run_agent_steps(run) == []

    agent_run = service._discover_agent_run(run)
    service._agent_step(agent_run, "planner", "completed", "Planned")
    service._agent_step(agent_run, "critic", "completed", "Critic reviewed")

    steps = service._run_agent_steps(run)
    assert [s.stage for s in steps] == ["planner", "critic"]
    assert steps[0].sequence == 1
    assert steps[1].sequence == 2


# ------------------------------------------------------------------ CriticAgent
def test_critic_review_parses_verdicts(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    llm = _CriticLLM(
        [
            {"index": 0, "verdict": "keep", "challenges": []},
            {"index": 1, "verdict": "narrow", "challenges": ["focus is too broad"], "suggested_narrowing": "restrict to node-level shift"},
        ]
    )
    service = _service(db_session, llm)
    run = _run(workspace_id)
    db_session.add(run)
    db_session.commit()
    candidates = [{"title": "A", "problem_statement": "..."}, {"title": "B", "problem_statement": "..."}]

    reviews = service._critic_review(
        run, "claim", candidates,
        _empty_response(workspace_id), _empty_response(workspace_id), _empty_response(workspace_id),
    )
    assert len(reviews) == 2
    assert reviews[0]["verdict"] == "keep"
    assert reviews[1]["verdict"] == "narrow"
    assert reviews[1]["challenges"] == ["focus is too broad"]
    assert "claim" in llm.messages[0][-1]["content"]


def test_critic_review_bad_shape_returns_empty(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    run = _run(workspace_id)
    db_session.add(run)
    db_session.commit()
    service = _service(db_session)  # _NoopLLM returns {"roles": []}
    reviews = service._critic_review(
        run, "claim", [{"title": "A", "problem_statement": "..."}],
        _empty_response(workspace_id), _empty_response(workspace_id), _empty_response(workspace_id),
    )
    assert reviews == []


def test_critic_review_llm_failure_returns_empty(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    run = _run(workspace_id)
    db_session.add(run)
    db_session.commit()
    service = _service(db_session, _BoomLLM())
    reviews = service._critic_review(
        run, "claim", [{"title": "A", "problem_statement": "..."}],
        _empty_response(workspace_id), _empty_response(workspace_id), _empty_response(workspace_id),
    )
    assert reviews == []


def test_critic_review_ignores_out_of_range_index(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    llm = _CriticLLM([{"index": 99, "verdict": "reject"}])
    service = _service(db_session, llm)
    run = _run(workspace_id)
    db_session.add(run)
    db_session.commit()
    reviews = service._critic_review(
        run, "claim", [{"title": "A", "problem_statement": "..."}],
        _empty_response(workspace_id), _empty_response(workspace_id), _empty_response(workspace_id),
    )
    assert reviews == []


def test_apply_critic_reviews_downweights_weak_candidates(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    service = _service(db_session)
    candidates = [
        {"title": "A", "problem_statement": "x", "confidence": 0.8},
        {"title": "B", "problem_statement": "x", "confidence": 0.8},
        {"title": "C", "problem_statement": "x", "confidence": 0.8},
    ]
    reviews = [
        {"index": 0, "verdict": "keep"},
        {"index": 1, "verdict": "narrow"},
        {"index": 2, "verdict": "reject"},
    ]
    counts = service._apply_critic_reviews(candidates, reviews)
    assert counts == {"keep": 1, "narrow": 1, "reject": 1}
    assert candidates[0]["confidence"] == 0.8
    assert candidates[0]["critic_review"]["verdict"] == "keep"  # index 0 must attach too
    assert candidates[1]["confidence"] == 0.45
    assert candidates[2]["confidence"] == 0.3
    assert candidates[2]["critic_review"]["verdict"] == "reject"


# ------------------------------------------------------------------ narrowing loop
def test_narrowing_obstacle_requires_strong_counter(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    service = _service(db_session)
    # strong contradicts → obstacle
    assert service._narrowing_obstacle(RetrievalResponse(workspace_id=workspace_id, purpose="counter_evidence", status="succeeded", items=[_counter_item("contradicts", 0.8)]))
    # weak / unknown → not an obstacle
    assert not service._narrowing_obstacle(RetrievalResponse(workspace_id=workspace_id, purpose="counter_evidence", status="succeeded", items=[_counter_item("contradicts", 0.3)]))
    assert not service._narrowing_obstacle(RetrievalResponse(workspace_id=workspace_id, purpose="counter_evidence", status="succeeded", items=[_counter_item("overlaps", 0.9)]))
    assert not service._narrowing_obstacle(RetrievalResponse(workspace_id=workspace_id, purpose="counter_evidence", status="succeeded", items=[]))


def test_narrowing_pass_records_obstacle_and_downweights(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    retrieval = _FakeRetrieval(counter_items=[_counter_item("qualifies", 0.8)])
    service = DiscoverService(db_session, retrieval=retrieval, external_search=_S2Fake({}), llm=_NoopLLM())
    run = _run(workspace_id)
    db_session.add(run)
    db_session.commit()
    candidates = [{"title": "A", "candidate_research_question": "Does X generalize?", "confidence": 0.6}]
    reviews = [{"index": 0, "verdict": "narrow", "suggested_narrowing": "restrict to node-level shift"}]

    narrowed = service._narrowing_pass(run, candidates, reviews)
    assert narrowed == 1
    assert candidates[0]["narrowing_pass"]["outcome"] == "obstacle_found"
    assert candidates[0]["confidence"] == 0.25
    assert retrieval.counter_calls
    assert "node-level shift" in retrieval.counter_calls[0]["claim"]


def test_narrowing_pass_direction_clear_when_no_obstacle(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    retrieval = _FakeRetrieval(counter_items=[])
    service = DiscoverService(db_session, retrieval=retrieval, external_search=_S2Fake({}), llm=_NoopLLM())
    run = _run(workspace_id)
    db_session.add(run)
    db_session.commit()
    candidates = [{"title": "A", "candidate_research_question": "Does X generalize?", "confidence": 0.6}]
    reviews = [{"index": 0, "verdict": "narrow", "suggested_narrowing": "restrict to node-level shift"}]

    narrowed = service._narrowing_pass(run, candidates, reviews)
    assert narrowed == 1
    assert candidates[0]["narrowing_pass"]["outcome"] == "direction_clear"
    assert candidates[0]["confidence"] == 0.6  # no obstacle → no further down-weight


def test_narrowing_pass_skips_keep_and_reject(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    retrieval = _FakeRetrieval()
    service = DiscoverService(db_session, retrieval=retrieval, external_search=_S2Fake({}), llm=_NoopLLM())
    run = _run(workspace_id)
    db_session.add(run)
    db_session.commit()
    candidates = [
        {"title": "A", "candidate_research_question": "Q1", "confidence": 0.8},
        {"title": "B", "candidate_research_question": "Q2", "confidence": 0.8},
        {"title": "C", "candidate_research_question": "Q3", "confidence": 0.8},
    ]
    reviews = [
        {"index": 0, "verdict": "keep"},
        {"index": 1, "verdict": "reject"},
        {"index": 2, "verdict": "narrow", "suggested_narrowing": "focus only on concept shift"},
    ]
    narrowed = service._narrowing_pass(run, candidates, reviews)
    assert narrowed == 1
    assert "narrowing_pass" not in candidates[0]
    assert "narrowing_pass" not in candidates[1]
    assert candidates[2]["narrowing_pass"]["outcome"] == "direction_clear"


# ------------------------------------------------------------------ Evidence Passport
def test_build_evidence_manifest_aggregates_counts(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    opp = ResearchOpportunity(
        id=str(uuid4()), workspace_id=workspace_id, title="T", summary="S", rationale="R",
        suggested_directions=[], confidence=0.5, status="candidate",
        source_payload={
            "gate": {"verified": False, "confirmable": True, "evidence_coverage": 0.5},
            "critic_review": {"verdict": "narrow"},
            "narrowing_pass": {"outcome": "direction_clear"},
        },
    )
    version = OpportunityVersion(
        id=str(uuid4()), opportunity_id=opp.id, version_number=1, title="T", problem_statement="P",
        created_at=datetime.now(UTC),
        verification_status="verified_with_warnings",
        synthesis_metadata={"provider": "remote", "prompt_version": "discover-v1"},
    )
    opp.current_version_id = version.id
    db_session.add_all([opp, version])
    db_session.flush()
    evidence = [
        OpportunityEvidence(opportunity_version_id=version.id, relation="supports", source_scope="workspace", evidence_level="full_text", paper_id="p1", rank=1, display_excerpt="a"),
        OpportunityEvidence(opportunity_version_id=version.id, relation="supports", source_scope="workspace", evidence_level="full_text", paper_id="p2", rank=2, display_excerpt="b"),
        OpportunityEvidence(opportunity_version_id=version.id, relation="similar", source_scope="workspace", evidence_level="metadata_only", paper_id="p3", rank=3, display_excerpt="c"),
        OpportunityEvidence(opportunity_version_id=version.id, relation="qualifies", source_scope="external", evidence_level="metadata_only", paper_id="p4", rank=4, display_excerpt="d"),
    ]
    db_session.add_all(evidence)
    db_session.commit()

    service = _service(db_session)
    manifest = service._build_evidence_manifest(opp, version, evidence)
    assert manifest is not None
    assert manifest.total == 4
    assert manifest.supports == 2
    assert manifest.similar == 1
    assert manifest.counter == 1
    assert manifest.independent_papers == 4
    assert manifest.full_text_papers == 2
    assert manifest.metadata_only_papers == 2
    assert manifest.external_sources == 1
    assert manifest.gate_verified is False
    assert manifest.gate_confirmable is True
    assert manifest.evidence_coverage == 0.5
    assert manifest.critic_verdict == "narrow"
    assert manifest.narrowing_outcome == "direction_clear"
    assert manifest.human_status == "candidate"
    assert manifest.prompt_version == "discover-v1"
    assert manifest.model_name == "remote"
    assert manifest.verification_status == "verified_with_warnings"
    assert manifest.evidence_freshness == "current"
    assert manifest.evidence_checked_at is not None
    assert len(manifest.items) == 4
    # the manifest is a snapshot, not tied to a new table
    assert db_session.get(type(opp), opp.id) is not None


def test_build_evidence_manifest_none_without_version(db_session) -> None:
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    opp = ResearchOpportunity(id=str(uuid4()), workspace_id=workspace_id, title="T", summary="S", rationale="R", suggested_directions=[], confidence=0.4, status="candidate", source_payload={})
    db_session.add(opp)
    db_session.commit()
    service = _service(db_session)
    assert service._build_evidence_manifest(opp, None, []) is None


def test_evidence_freshness_uses_revalidation_snapshot_age(db_session) -> None:
    service = _service(db_session)
    now = datetime(2026, 2, 1, tzinfo=UTC)

    def evidence_at(days_old: int) -> OpportunityEvidence:
        return OpportunityEvidence(
            created_at=now - timedelta(days=days_old),
            opportunity_version_id="version",
            relation="supports",
            source_scope="workspace",
            evidence_level="full_text",
        )

    status, checked_at = service._evidence_freshness([evidence_at(30)], now=now)
    assert status == "current"
    assert checked_at == now - timedelta(days=30)

    status, _ = service._evidence_freshness([evidence_at(31)], now=now)
    assert status == "stale"

    status, _ = service._evidence_freshness([evidence_at(61)], now=now)
    assert status == "expired"

    status, checked_at = service._evidence_freshness([], now=now)
    assert status == "unknown"
    assert checked_at is None


def test_find_evidence_span_strips_nul_from_query(db_session) -> None:
    """PostgreSQL rejects NUL in LIKE parameters; _find_evidence_span must
    strip NUL bytes from the retrieved chunk text before querying."""
    workspace_id = str(uuid4())
    _workspace(db_session, workspace_id)
    paper_id = str(uuid4())
    span_text = "Lexpl(e, G, y) := BCE e(G)u"
    db_session.add(
        EvidenceSpan(
            workspace_id=workspace_id,
            paper_id=paper_id,
            knowledge_item_id=str(uuid4()),
            artifact_kind="parsed_markdown",
            text=span_text,
            relation="supports",
            confidence=0.9,
        )
    )
    db_session.commit()
    service = _service(db_session)
    item = RetrievalResultItem(
        paper_id=paper_id,
        paper_title="P",
        artifact_id=None,
        chunk_id="c1",
        text="Lexpl(e, G, y) := \x00BCE e(G)u",  # NUL from an old parse
        evidence_level="full_text",
        judgement="supports",
        source_scope="workspace",
    )
    span = service._find_evidence_span(item, workspace_id)  # must not raise
    assert span is not None
    assert span.text == span_text
