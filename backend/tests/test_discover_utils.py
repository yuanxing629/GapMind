"""discover 共用 utils 测试（W6-3 token 统计 + NUL 清理）。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.domains.discover.models import DiscoverRun  # noqa: E402
from app.domains.discover.utils import accumulate_tokens, parse_json, retrieval_payload  # noqa: E402
from app.domains.retrieval.schemas import RetrievalResultItem  # noqa: E402
from app.domains.workspace.models import Workspace  # noqa: E402


def _run(db) -> DiscoverRun:
    ws = Workspace(id=str(uuid4()), name="ws")
    db.add(ws)
    db.commit()
    r = DiscoverRun(id=str(uuid4()), workspace_id=ws.id, status="running", input_payload={}, scope={}, config={}, stage_summaries={})
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def test_accumulate_tokens_sums_into_run_stage_summaries(db_session):
    run = _run(db_session)
    accumulate_tokens(run, SimpleNamespace(prompt_tokens=100, completion_tokens=50))
    accumulate_tokens(run, SimpleNamespace(prompt_tokens=40, completion_tokens=10))
    tu = run.stage_summaries["token_usage"]
    assert tu["prompt_tokens"] == 140
    assert tu["completion_tokens"] == 60
    assert tu["total_tokens"] == 200


def test_accumulate_tokens_accepts_dict_and_noops_without_usage(db_session):
    run = _run(db_session)
    accumulate_tokens(run, {"prompt_tokens": 7, "completion_tokens": 3})
    assert run.stage_summaries["token_usage"]["total_tokens"] == 10
# 没有 usage 信息 -> no-op，不崩溃。
    accumulate_tokens(run, SimpleNamespace(content="x"))
    assert run.stage_summaries["token_usage"]["total_tokens"] == 10


def test_parse_json_tolerates_code_fence(db_session):
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('{"a": 1}') == {"a": 1}
    assert parse_json("not json") is None


def test_retrieval_payload_compact(db_session):
    item = RetrievalResultItem(
        paper_id="p1", paper_title="T", chunk_id="c1", text="x" * 2000, score=0.9,
        judgement="supports", evidence_level="full_text",
    )
    payload = retrieval_payload(item)
    assert payload["paper_id"] == "p1"
    assert len(payload["text"]) <= 900


def test_synthesize_accumulates_tokens_into_run(db_session):
    """真实 LLM 调用路径（synthesis）必须在运行记录中累计 token 用量。"""
    import json as _json

    from app.domains.discover.service import DiscoverService
    from app.domains.retrieval.schemas import RetrievalResponse

    ws = Workspace(id=str(uuid4()), name="ws")
    db_session.add(ws)
    db_session.commit()
    run = DiscoverRun(id=str(uuid4()), workspace_id=ws.id, status="running", input_payload={}, scope={}, config={}, stage_summaries={})
    db_session.add(run)
    db_session.commit()

    class _UsageLLM:
        def chat_completion(self, messages, **kwargs):
            return SimpleNamespace(
                content=_json.dumps({
                    "opportunities": [{
                        "title": "T", "problem_statement": "p", "research_scope": "s",
                        "why_existing_work_is_insufficient": "w", "candidate_research_question": "q",
                        "candidate_hypothesis": "h", "candidate_validation_plan": {"steps": []},
                        "open_risks": [], "novelty_score": 0.6, "feasibility_score": 0.6,
                        "significance_score": 0.6, "confidence": 0.5,
                    }]
                }),
                prompt_tokens=120,
                completion_tokens=80,
                total_tokens=200,
            )

    svc = DiscoverService(db_session, llm=_UsageLLM())
    empty = lambda p: RetrievalResponse(workspace_id=ws.id, purpose=p, status="succeeded", items=[])
    gate = {"verified": False, "confirmable": False, "evidence_coverage": 0.0}
    svc._synthesize_candidates(run, "topic", empty("supporting"), empty("similar"), empty("counter"), empty("external_full_text"), gate, 3)

    tu = run.stage_summaries["token_usage"]
    assert tu["prompt_tokens"] == 120
    assert tu["completion_tokens"] == 80
    assert tu["total_tokens"] == 200


def test_hit_to_result_item_strips_nul_bytes(db_session):
    """检索项文本不能携带 NUL 字节（PostgreSQL 会拒绝它们）。"""
    from app.domains.retrieval.service import _hit_to_result_item

    nul = chr(0)
    ff = chr(12)  # form feed, allowed by PostgreSQL
    item = _hit_to_result_item(
        {"paper_id": "p1", "chunk_id": "c1", "source_artifact_id": "a1",
         "text": "alpha" + nul + "beta" + ff + "gamma", "score": 0.5}
    )
    assert nul not in item.text
    assert item.text == "alphabeta" + ff + "gamma"  # form feed kept; NUL removed
