"""OpportunityAgent (MA + W2): synthesis of candidate research opportunities.

Carved out of the monolithic DiscoverService (MA-1 maintenance refactor) so
the LLM synthesis step (evidence → candidate proposals) is self-contained and
individually testable. DiscoverService instantiates one and delegates its
existing ``_synthesize_candidates`` / ``_normalize_candidate`` /
``_fallback_candidate`` / ``_retrieval_payload`` methods to it.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.discover.models import DiscoverRun
from app.domains.discover.utils import accumulate_tokens, parse_json, retrieval_payload
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem
from app.gateway.llm import LLMGateway

logger = get_logger(__name__)

# System prompt used by OpportunityAgent synthesis. Kept as a module constant
# so prompt changes are auditable and testable.
SYNTHESIS_SYSTEM_PROMPT = "你负责生成可审计的中文研究机会方案；证据原文必须保持不变。"



# --------------------------------------------------------------------- helpers



def normalize_candidate(
    value: dict[str, Any], gate: dict[str, Any], *, provider: str
) -> dict[str, Any]:
    """Normalize one raw LLM candidate into the persisted opportunity shape."""

    def score(key: str) -> float:
        try:
            return max(0.0, min(1.0, float(value.get(key, 0.35 if not gate["verified"] else 0.55))))
        except (TypeError, ValueError):
            return 0.35

    plan = value.get("candidate_validation_plan")
    if not isinstance(plan, dict):
        plan = {"steps": ["选择数据集与基线方法", "与最强的相似工作设置进行比较", "针对推测的边界条件开展消融实验"]}
    risks = value.get("open_risks")
    if not isinstance(risks, list):
        risks = ["外部论文全文核验尚未完成。"]
    confidence = score("confidence")
    return {
        "title": str(value.get("title") or "研究该主题成立与失效的边界条件")[:512],
        "problem_statement": str(value.get("problem_statement") or "现有证据尚不足以确定该现象可推广到哪些条件。"),
        "research_scope": str(value.get("research_scope") or "研究范围应限定在当前工作区已有的数据集、模型与约束条件内。"),
        "why_existing_work_is_insufficient": str(value.get("why_existing_work_is_insufficient") or "现有工作尚未在统一条件下进行充分比较。"),
        "candidate_research_question": str(value.get("candidate_research_question") or "在什么条件下，该现象仍然可靠？"),
        "candidate_hypothesis": str(value.get("candidate_hypothesis") or "在工作区证据所覆盖的假设条件下，该现象预计最为显著。"),
        "candidate_validation_plan": plan,
        "open_risks": [str(item) for item in risks[:8]],
        "novelty_score": score("novelty_score"),
        "feasibility_score": score("feasibility_score"),
        "significance_score": score("significance_score"),
        "confidence": confidence,
        "evidence_coverage": float(gate.get("evidence_coverage", 0.0)),
        "verification_status": "verified" if gate["verified"] else ("verified_with_warnings" if gate.get("confirmable") else "verification_incomplete"),
        "provider": provider,
    }


def fallback_candidate(
    claim_text: str,
    supporting: RetrievalResponse,
    similar: RetrievalResponse,
    counter: RetrievalResponse,
    gate: dict[str, Any],
) -> dict[str, Any]:
    """Conservative rule-based candidate used when LLM synthesis fails (W5)."""
    return normalize_candidate(
        {
            "title": "研究该论断成立与失效的边界条件",
            "problem_statement": "该论断具有一定合理性，但其成立的边界条件尚未明确。",
            "why_existing_work_is_insufficient": f"工作区检索到 {len(supporting.items)} 条支持证据、{len(similar.items)} 条相似工作证据和 {len(counter.items)} 条反证，但最终证据门槛尚未满足。",
            "candidate_research_question": f"以下论断在什么条件下成立，又会在什么条件下失效？{claim_text[:500]}",
            "candidate_hypothesis": "该效应取决于某个可测量的数据或模型条件，并可通过消融实验加以分离验证。",
            "open_risks": ["外部元数据不能替代全文证据。", "当前检索结果可能不完整。"],
        },
        gate,
        provider="rule_based_fallback",
    )


# --------------------------------------------------------------------- service


class SynthesisService:
    """OpportunityAgent synthesis orchestration.

    Composed by ``DiscoverService``; callers should go through that facade so
    existing tests using ``service._synthesize_candidates`` keep working.
    """

    def __init__(self, db: Session, llm: LLMGateway) -> None:
        self.db = db
        self.llm = llm

    def synthesize(
        self,
        run: DiscoverRun,
        claim_text: str,
        supporting: RetrievalResponse,
        similar: RetrievalResponse,
        counter: RetrievalResponse,
        external_fulltext: RetrievalResponse,
        gate: dict[str, Any],
        maximum: int,
        *,
        critic_feedback: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Generate candidate opportunities from workspace + external evidence.

        On LLM failure or a malformed response, degrades to a conservative
        rule-based fallback candidate so the pipeline never fails (W5).
        """
        evidence = {
            "supporting_evidence": [retrieval_payload(item) for item in supporting.items[:12]],
            "external_full_text": [retrieval_payload(item) for item in external_fulltext.items[:12]],
            "similar_work": [retrieval_payload(item) for item in similar.items[:12]],
            "counter_evidence": [retrieval_payload(item) for item in counter.items[:12]],
            "gate": gate,
            "constraints": (run.input_payload or {}).get("constraints"),
            "critic_feedback": critic_feedback or [],
        }
        prompt = (
            "You are a conservative research-discovery agent. Return ONLY JSON with an "
            "opportunities array. Each item must include title, problem_statement, "
            "research_scope, why_existing_work_is_insufficient, candidate_research_question, "
            "candidate_hypothesis, candidate_validation_plan, open_risks, novelty_score, "
            "feasibility_score, significance_score, confidence. Do not invent papers. "
            "Keep supporting_evidence, similar_work, counter_evidence, and external_full_text "
            "as separate roles; similar_work is never supporting evidence. "
            "If evidence is incomplete, explicitly say verification is incomplete and keep "
            "scores conservative. "
            "If CRITIC_FEEDBACK is non-empty, address each listed challenge explicitly: "
            "the proposal must respond to the critic's gaps rather than repeat the same "
            "weakness. Write every generated proposal field in Simplified Chinese, "
            "including the title, problem statement, scope, insufficiency analysis, research "
            "question, hypothesis, validation steps, and risks. Keep paper titles, evidence "
            "excerpts, citations, identifiers, and JSON keys in their original form; do not "
            "translate or rewrite quoted evidence.\n\nCLAIM_OR_TOPIC:\n" + claim_text[:3000] +
            "\n\nEVIDENCE:\n" + json.dumps(evidence, ensure_ascii=False)
        )
        try:
            response = self.llm.chat_completion(
                [
                    {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2200,
                disable_thinking=True,  # structured JSON — avoid CoT burning the budget
            )
            accumulate_tokens(run, response)
            parsed = parse_json(response.content)
            raw_items = parsed.get("opportunities") if isinstance(parsed, dict) else None
            if isinstance(raw_items, dict):
                raw_items = [raw_items]
            if isinstance(raw_items, list):
                normalized = [
                    normalize_candidate(item, gate, provider="remote")
                    for item in raw_items
                    if isinstance(item, dict)
                ]
                if normalized:
                    return normalized[:maximum]
        except Exception as exc:
            logger.warning("discover.synthesis_fallback", run_id=run.id, error=str(exc))
        return [fallback_candidate(claim_text, supporting, similar, counter, gate)]
