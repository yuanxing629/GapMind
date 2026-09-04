"""面向已保存 Workspace Chat 回答的纯函数、来源感知指标。"""

from __future__ import annotations

from collections import Counter
from math import ceil
from typing import Any

from app.domains.chat.consistency import message_citation_check, source_marker_check
from evaluation.chat.gold_set import ChatQAObservation, ChatQAQuestion


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(len(ordered) * percentile) - 1))
    return ordered[index]


def _normalized_refs(refs: list[str]) -> set[str]:
    return {ref.strip().casefold() for ref in refs if ref and ref.strip()}


def assess_answer(question: ChatQAQuestion, observation: ChatQAObservation) -> dict[str, Any]:
    """评估一条已保存回答，不解释或重新生成其文本。

    标记解析器从 Chat domain 导入，因此离线评估器和 API 使用相同的
    ``[En]`` / ``[Pn]`` / ``[Dn]`` / ``[Cn]`` 契约。``human_verdict`` 仍然是显式标注：
    评估器不会根据 LLM 响应推断事实正确性。
    """

    evidence_by_rank = {item.rank: item.paper_ref for item in observation.evidence}
    paper_check = message_citation_check(
        observation.answer_text,
        list(evidence_by_rank),
        grounded=observation.grounding_status == "grounded",
    )
    source_check = source_marker_check(
        observation.answer_text,
        {f"[{item.marker}]" for item in observation.sources},
    )
    cited_paper_refs = [evidence_by_rank[rank] for rank in paper_check.valid]
    required_refs = _normalized_refs(question.required_paper_refs)
    cited_refs = _normalized_refs(cited_paper_refs)
    required_hits = sorted(required_refs & cited_refs)
    paper_coverage = len(required_hits) / len(required_refs) if required_refs else None
    requires_paper_evidence = question.expected_verdict == "supported"
    has_valid_paper_citation = bool(paper_check.valid)
    requires_confirmed_plan = question.context.mode == "workspace_with_confirmed_plan"
    has_confirmed_plan_source = any(item.source_type == "plan" for item in observation.sources)
    cites_confirmed_plan = any(marker.startswith("[P") for marker in source_check.referenced)
    plan_context_ok = (
        not requires_confirmed_plan
        or (has_confirmed_plan_source and cites_confirmed_plan and source_check.ok)
    )
    mechanical_passed = (
        paper_check.ok
        and source_check.ok
        and plan_context_ok
        and (not requires_paper_evidence or observation.grounding_status == "grounded")
        and (not requires_paper_evidence or has_valid_paper_citation)
        and (not requires_paper_evidence or paper_coverage == 1.0)
    )

    return {
        "query_id": question.query_id,
        "expected_verdict": question.expected_verdict,
        "human_verdict": observation.human_verdict,
        "human_verdict_match": (
            observation.human_verdict == question.expected_verdict
            if observation.human_verdict is not None
            else None
        ),
        "paper_marker_check": {
            "referenced": paper_check.referenced,
            "broken": paper_check.broken,
            "grounded_without_citations": paper_check.grounded_without_citations,
            "ok": paper_check.ok,
        },
        "source_marker_check": {
            "referenced": source_check.referenced,
            "broken": source_check.broken,
            "ok": source_check.ok,
        },
        "required_paper_refs": question.required_paper_refs,
        "cited_paper_refs": cited_paper_refs,
        "paper_coverage": paper_coverage,
        "retrieval_audit": (
            observation.retrieval_audit.model_dump(mode="json")
            if observation.retrieval_audit is not None
            else None
        ),
        "plan_context_required": requires_confirmed_plan,
        "plan_context_ok": plan_context_ok,
        "mechanical_passed": mechanical_passed,
    }


def build_report(gold, observations) -> dict[str, Any]:
    """为 Gold Set 和导出回答构建不设阈值的报告。

    这里不会隐藏任何相关性阈值。在有足够的人工复核样本之前，报告只展示覆盖率和判断准确率，
    不会假装某个单一启发式规则可以成为生产质量门禁。
    """

    observation_by_query = {item.query_id: item for item in observations.observations}
    expected_query_ids = {item.query_id for item in gold.questions}
    unknown_query_ids = sorted(set(observation_by_query) - expected_query_ids)
    entries: list[dict[str, Any]] = []

    for question in gold.questions:
        observation = observation_by_query.get(question.query_id)
        if observation is None:
            entries.append(
                {
                    "query_id": question.query_id,
                    "expected_verdict": question.expected_verdict,
                    "status": "missing_observation",
                    "mechanical_passed": False,
                }
            )
            continue
        entry = assess_answer(question, observation)
        entry["status"] = "observed"
        entries.append(entry)

    observed_entries = [entry for entry in entries if entry["status"] == "observed"]
    supported_entries = [
        entry for entry in observed_entries if entry["expected_verdict"] == "supported"
    ]
    paper_coverages = [entry["paper_coverage"] for entry in supported_entries]
    human_matches = [
        entry["human_verdict_match"]
        for entry in observed_entries
        if entry["human_verdict_match"] is not None
    ]
    citation_valid_entries = [
        entry
        for entry in supported_entries
        if entry["paper_marker_check"]["ok"]
        and not entry["paper_marker_check"]["grounded_without_citations"]
        and bool(entry["paper_marker_check"]["referenced"])
    ]
    source_valid_entries = [
        entry for entry in observed_entries if entry["source_marker_check"]["ok"]
    ]
    plan_context_entries = [
        entry for entry in observed_entries if entry["plan_context_required"]
    ]
    observed_observations = [
        observation_by_query[question.query_id]
        for question in gold.questions
        if question.query_id in observation_by_query
    ]
    audit_snapshots = [
        observation.retrieval_audit
        for observation in observed_observations
        if observation.retrieval_audit is not None
    ]
    audit_latencies = [snapshot.latency_ms for snapshot in audit_snapshots]
    retrieved_entries = [
        entry
        for entry in observed_entries
        if entry["retrieval_audit"] is not None
        and entry["retrieval_audit"]["returned_chunk_count"] > 0
    ]
    retrieved_without_paper_citation = [
        entry for entry in retrieved_entries if not entry["paper_marker_check"]["referenced"]
    ]
    mechanical_passed = not unknown_query_ids and len(observed_entries) == len(entries) and all(
        entry["mechanical_passed"] for entry in observed_entries
    )

    return {
        "case_id": gold.case_id,
        "corpus_version": gold.corpus_version,
        "annotation_status": gold.annotation_status,
        "freeze": gold.freeze.model_dump(),
        "summary": {
            "questions": len(entries),
            "observed_questions": len(observed_entries),
            "missing_questions": len(entries) - len(observed_entries),
            "unknown_observation_query_ids": unknown_query_ids,
            "paper_citation_validity_rate": (
                len(citation_valid_entries) / len(supported_entries)
                if supported_entries
                else None
            ),
            "mean_required_paper_coverage": (
                sum(paper_coverages) / len(paper_coverages) if paper_coverages else None
            ),
            "source_marker_validity_rate": (
                len(source_valid_entries) / len(observed_entries) if observed_entries else None
            ),
            "confirmed_plan_context_validity_rate": (
                sum(entry["plan_context_ok"] for entry in plan_context_entries)
                / len(plan_context_entries)
                if plan_context_entries
                else None
            ),
            "human_verdict_coverage": (
                len(human_matches) / len(observed_entries) if observed_entries else None
            ),
            "human_verdict_accuracy": (
                sum(human_matches) / len(human_matches) if human_matches else None
            ),
            "retrieval_audit_coverage": (
                len(audit_snapshots) / len(observed_entries) if observed_entries else None
            ),
            "retrieval_status_counts": dict(
                sorted(Counter(snapshot.status for snapshot in audit_snapshots).items())
            ),
            "reranker_status_counts": dict(
                sorted(Counter(snapshot.reranker_status for snapshot in audit_snapshots).items())
            ),
            "retrieval_latency_ms": {
                "count": len(audit_latencies),
                "p50": _nearest_rank(audit_latencies, 0.50),
                "p95": _nearest_rank(audit_latencies, 0.95),
                "max": max(audit_latencies) if audit_latencies else None,
            },
            "retrieved_without_paper_citation_count": len(retrieved_without_paper_citation),
            "retrieved_without_paper_citation_rate": (
                len(retrieved_without_paper_citation) / len(retrieved_entries)
                if retrieved_entries
                else None
            ),
            "mechanical_passed": mechanical_passed,
        },
        "items": entries,
    }


__all__ = ["assess_answer", "build_report"]
