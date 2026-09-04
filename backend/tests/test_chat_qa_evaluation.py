"""离线 Workspace Chat QA 评测基线测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evaluation.chat.gold_set import (  # noqa: E402
    ChatQAGoldSet,
    ChatQAObservation,
    ChatQAObservationSet,
    ChatQAQuestion,
)
from evaluation.chat.metrics import assess_answer, build_report  # noqa: E402
from evaluation.chat.run_eval import main as run_chat_qa_eval  # noqa: E402


def _question(**overrides) -> ChatQAQuestion:
    data = {
        "query_id": "q1",
        "question": "ProtGNN 和 PGIB 的方法差异是什么？",
        "expected_verdict": "supported",
        "required_paper_refs": ["ProtGNN", "PGIB"],
    }
    data.update(overrides)
    return ChatQAQuestion.model_validate(data)


def _observation(**overrides) -> ChatQAObservation:
    data = {
        "query_id": "q1",
        "answer_text": "ProtGNN 使用原型 [E1]，PGIB 使用信息瓶颈 [E2]，计划见 [P1]。",
        "grounding_status": "grounded",
        "evidence": [
            {"rank": 1, "paper_ref": "ProtGNN"},
            {"rank": 2, "paper_ref": "PGIB"},
        ],
        "sources": [{"marker": "P1", "source_type": "plan", "title": "确认计划"}],
        "human_verdict": "supported",
    }
    data.update(overrides)
    return ChatQAObservation.model_validate(data)


def _gold(question: ChatQAQuestion) -> ChatQAGoldSet:
    return ChatQAGoldSet(
        case_id="chat-case",
        corpus_version="test-corpus-v1",
        annotation_status="gold",
        questions=[question],
    )


def test_supported_question_requires_paper_reference() -> None:
    with pytest.raises(ValidationError, match="required_paper_ref"):
        _question(required_paper_refs=[])


def test_insufficient_evidence_question_must_not_predeclare_papers() -> None:
    with pytest.raises(ValidationError, match="must not declare"):
        _question(expected_verdict="insufficient_evidence", required_paper_refs=["ProtGNN"])


def test_assess_answer_accepts_real_paper_and_plan_markers() -> None:
    result = assess_answer(_question(), _observation())

    assert result["paper_marker_check"]["broken"] == []
    assert result["source_marker_check"]["broken"] == []
    assert result["paper_coverage"] == 1.0
    assert result["human_verdict_match"] is True
    assert result["mechanical_passed"] is True


def test_assess_answer_rejects_hallucinated_paper_or_source_marker() -> None:
    result = assess_answer(
        _question(),
        _observation(answer_text="论文 [E1]、[E9]，报告 [D1]。"),
    )

    assert result["paper_marker_check"]["broken"] == [9]
    assert result["source_marker_check"]["broken"] == ["[D1]"]
    assert result["mechanical_passed"] is False


def test_assess_answer_requires_all_gold_paper_refs_for_supported_question() -> None:
    result = assess_answer(
        _question(),
        _observation(answer_text="仅引用 ProtGNN [E1]。"),
    )

    assert result["paper_coverage"] == 0.5
    assert result["mechanical_passed"] is False


def test_assess_answer_requires_grounded_status_for_supported_question() -> None:
    result = assess_answer(
        _question(),
        _observation(grounding_status="plan_context"),
    )

    assert result["paper_marker_check"]["broken"] == []
    assert result["mechanical_passed"] is False


def test_retrieval_audit_is_reported_without_becoming_a_quality_verdict() -> None:
    observation = _observation(
        retrieval_audit={
            "request_id": "local-only-request-id",
            "status": "succeeded",
            "diagnostic_code": None,
            "recall_count": 18,
            "returned_chunk_count": 4,
            "final_paper_count": 4,
            "latency_ms": 986.83,
            "reranker_status": "applied",
        }
    )
    result = assess_answer(_question(), observation)

    assert result["retrieval_audit"]["status"] == "succeeded"
    assert result["retrieval_audit"]["recall_count"] == 18
    assert result["mechanical_passed"] is True

    gold = _gold(_question())
    report = build_report(
        gold,
        ChatQAObservationSet(gold_case_id=gold.case_id, observations=[observation]),
    )
    assert report["summary"]["retrieval_audit_coverage"] == 1.0
    assert report["summary"]["retrieval_status_counts"] == {"succeeded": 1}
    assert report["summary"]["reranker_status_counts"] == {"applied": 1}
    assert report["summary"]["retrieval_latency_ms"] == {
        "count": 1,
        "p50": 986.83,
        "p95": 986.83,
        "max": 986.83,
    }
    assert report["summary"]["retrieved_without_paper_citation_count"] == 0
    assert report["summary"]["retrieved_without_paper_citation_rate"] == 0.0


def test_assess_answer_requires_a_real_plan_marker_when_plan_context_is_selected() -> None:
    question = _question(
        context={
            "mode": "workspace_with_confirmed_plan",
            "research_plan_ref": "GNN explanation plan v1",
        }
    )
    result = assess_answer(
        question,
        _observation(answer_text="ProtGNN [E1]，PGIB [E2]。"),
    )

    assert result["plan_context_required"] is True
    assert result["plan_context_ok"] is False
    assert result["mechanical_passed"] is False


def test_chat_gold_requires_a_plan_reference_for_plan_context() -> None:
    with pytest.raises(ValidationError, match="requires research_plan_ref"):
        _question(context={"mode": "workspace_with_confirmed_plan"})


def test_build_report_exposes_manual_and_mechanical_metrics() -> None:
    gold = _gold(_question())
    observations = ChatQAObservationSet(gold_case_id=gold.case_id, observations=[_observation()])

    report = build_report(gold, observations)

    assert report["summary"]["paper_citation_validity_rate"] == 1.0
    assert report["summary"]["mean_required_paper_coverage"] == 1.0
    assert report["summary"]["source_marker_validity_rate"] == 1.0
    assert report["summary"]["human_verdict_accuracy"] == 1.0
    assert report["summary"]["mechanical_passed"] is True


def test_build_report_fails_when_gold_question_has_no_observation() -> None:
    gold = _gold(_question())
    other = _observation(query_id="other")
    observations = ChatQAObservationSet(gold_case_id=gold.case_id, observations=[other])

    report = build_report(gold, observations)

    assert report["summary"]["missing_questions"] == 1
    assert report["summary"]["unknown_observation_query_ids"] == ["other"]
    assert report["summary"]["mechanical_passed"] is False


def test_runner_scores_local_snapshot_without_calling_chat(tmp_path: Path) -> None:
    gold = _gold(_question())
    observations = ChatQAObservationSet(gold_case_id=gold.case_id, observations=[_observation()])
    gold_path = tmp_path / "gold.json"
    observations_path = tmp_path / "observations.json"
    report_path = tmp_path / "report.json"
    gold_path.write_text(json.dumps(gold.model_dump()), encoding="utf-8")
    observations_path.write_text(json.dumps(observations.model_dump()), encoding="utf-8")

    result = run_chat_qa_eval(
        ["--gold", str(gold_path), "--observations", str(observations_path), "--output", str(report_path)]
    )

    assert result == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["mechanical_passed"] is True


def test_committed_draft_gold_set_has_a_valid_schema() -> None:
    gold_path = _REPO_ROOT / "evaluation" / "chat" / "gold" / "gnn_explanations_draft_v1.json"

    gold = ChatQAGoldSet.model_validate_json(gold_path.read_text(encoding="utf-8"))

    assert gold.annotation_status == "draft"
    assert len(gold.questions) == 2
