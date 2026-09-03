"""W2 opportunity-quality tests.

Covers: critic-challenge injection into the Opportunity synthesis prompt,
critic challenge collection (narrow/reject, dedup, bounded), and the audit
trail on DiscoverRun (prompt_version / corpus_version fingerprint).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.domains.discover.models import DiscoverRun  # noqa: E402
from app.domains.discover.schemas import DiscoverInput, DiscoverRunCreateRequest  # noqa: E402
from app.domains.discover.service import DISCOVER_PROMPT_VERSION, DiscoverService  # noqa: E402
from app.domains.knowledge.models import KnowledgeItem  # noqa: E402
from app.domains.paper.models import Paper  # noqa: E402
from app.domains.retrieval.schemas import RetrievalResponse  # noqa: E402
from app.domains.timeline.models import TimelineEvent  # noqa: E402
from app.domains.workspace.models import Workspace  # noqa: E402


class _SynthLLM:
    """Records the user prompt; returns one valid opportunity."""

    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    def chat_completion(self, messages, **kwargs):
        self.messages.append(messages)
        return SimpleNamespace(content=json.dumps({
            "opportunities": [
                {
                    "title": "研究该现象成立与失效的边界条件",
                    "problem_statement": "现有证据不足以确定推广条件。",
                    "research_scope": "限定在当前工作区数据与约束内。",
                    "why_existing_work_is_insufficient": "缺乏统一条件比较。",
                    "candidate_research_question": "在什么条件下该现象仍可靠？",
                    "candidate_hypothesis": "在证据覆盖条件下最显著。",
                    "candidate_validation_plan": {"steps": ["消融"]},
                    "open_risks": ["反证不足。"],
                    "novelty_score": 0.7,
                    "feasibility_score": 0.7,
                    "significance_score": 0.7,
                    "confidence": 0.6,
                }
            ]
        }))


class _NoopLLM:
    def chat_completion(self, messages, **kwargs):
        return SimpleNamespace(content=json.dumps({"opportunities": []}))


def _service(db, llm=None) -> DiscoverService:
    return DiscoverService(db, llm=llm or _NoopLLM())


def _ws(db) -> Workspace:
    ws = Workspace(id=str(uuid4()), name="ws")
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws


def _run(db, ws: Workspace, **overrides) -> DiscoverRun:
    kwargs = {
        "id": str(uuid4()),
        "workspace_id": ws.id,
        "status": "running",
        "input_payload": {"topic": "topic"},
        "scope": {},
        "config": {},
        "stage_summaries": {},
    }
    kwargs.update(overrides)
    run = DiscoverRun(**kwargs)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def _empty_response(workspace_id: str, purpose: str) -> RetrievalResponse:
    return RetrievalResponse(workspace_id=workspace_id, purpose=purpose, status="succeeded", items=[])


# ----------------------------------------------------- critic feedback injection


def test_synthesize_injects_critic_feedback(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    llm = _SynthLLM()
    svc = _service(db_session, llm=llm)
    gate = {"verified": True, "confirmable": True, "evidence_coverage": 0.7}

    candidates = svc._synthesize_candidates(
        run, "topic",
        _empty_response(ws.id, "supporting"),
        _empty_response(ws.id, "similar"),
        _empty_response(ws.id, "counter"),
        _empty_response(ws.id, "external_full_text"),
        gate, 3,
        critic_feedback=["challenge A", "challenge B"],
    )

    assert len(candidates) == 1
    prompt = llm.messages[0][-1]["content"]
    assert "CRITIC_FEEDBACK" in prompt
    assert "challenge A" in prompt
    assert "challenge B" in prompt
    # Evidence payload carries the challenges verbatim.
    assert '"critic_feedback": ["challenge A", "challenge B"]' in prompt


def test_synthesize_without_critic_feedback_passes_empty(db_session):
    ws = _ws(db_session)
    run = _run(db_session, ws)
    llm = _SynthLLM()
    svc = _service(db_session, llm=llm)
    gate = {"verified": True, "confirmable": True, "evidence_coverage": 0.7}

    svc._synthesize_candidates(
        run, "topic",
        _empty_response(ws.id, "supporting"),
        _empty_response(ws.id, "similar"),
        _empty_response(ws.id, "counter"),
        _empty_response(ws.id, "external_full_text"),
        gate, 3,
    )

    prompt = llm.messages[0][-1]["content"]
    assert '"critic_feedback": []' in prompt


def test_critic_challenges_collects_narrow_reject_deduped():
    svc = DiscoverService.__new__(DiscoverService)
    reviews = [
        {"index": 0, "verdict": "keep", "challenges": ["k"]},
        {"index": 1, "verdict": "narrow", "challenges": ["narrow it to x", "also check y"]},
        {"index": 2, "verdict": "reject", "challenges": ["narrow it to x", "z"]},
    ]
    # keep is ignored; duplicates collapse; reject is collected too (bounded 3).
    assert svc._critic_challenges(reviews) == ["narrow it to x", "also check y", "z"]


def test_critic_challenges_bounded_and_empty_cases():
    svc = DiscoverService.__new__(DiscoverService)
    assert svc._critic_challenges([{"verdict": "reject", "challenges": ["1", "2", "3", "4"]}]) == ["1", "2", "3"]
    assert svc._critic_challenges([{"verdict": "keep", "challenges": ["a"]}]) == []
    assert svc._critic_challenges([]) == []
    assert svc._critic_challenges([{"verdict": "narrow", "challenges": []}]) == []


# ---------------------------------------------------------------- audit trail


def test_corpus_snapshot_fingerprint(db_session):
    ws = _ws(db_session)
    db_session.add(Paper(id=str(uuid4()), workspace_id=ws.id, title="p", authors=[], is_deleted=False))
    db_session.add(KnowledgeItem(id=str(uuid4()), workspace_id=ws.id, type="claim", canonical_name="c", is_deleted=False))
    db_session.commit()
    assert _service(db_session)._corpus_snapshot(ws.id) == "workspace-v1-1p-1k"


def test_create_run_stamps_audit_fields(db_session):
    ws = _ws(db_session)
    svc = _service(db_session)
    actor_id = str(uuid4())
    run, _ = svc.create_run(
        ws.id,
        DiscoverRunCreateRequest(input=DiscoverInput(topic="GNN explanation robustness")),
        actor=actor_id,
        trigger_type="topic",
    )
    db_session.refresh(run)
    assert run.prompt_version == DISCOVER_PROMPT_VERSION
    assert run.corpus_version == "workspace-v1-0p-0k"
    assert run.model_provider == "remote"
    assert run.model_name == "remote"
    event = db_session.scalar(
        select(TimelineEvent).where(
            TimelineEvent.workspace_id == ws.id,
            TimelineEvent.subject_id == run.id,
            TimelineEvent.event_type == "discover.run_created",
        )
    )
    assert event is not None
    assert event.actor == actor_id
