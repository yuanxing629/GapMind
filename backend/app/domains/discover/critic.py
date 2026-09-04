"""CriticAgent（MA + W2）：对抗式审查与收窄流程。

本模块从单体 DiscoverService 中拆出（MA-1 维护性重构），使多智能体 Critic 循环
（verdict → challenges → narrowing）自包含且可单独测试。DiscoverService 创建该服务，
并将原有的 ``_critic_*`` 方法转发给它，以保持既有测试的兼容性。
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.discover.models import DiscoverRun
from app.domains.discover.ports import RetrievalPort
from app.domains.discover.schemas import DiscoverConfig
from app.domains.discover.utils import accumulate_tokens, parse_json, retrieval_payload
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem
from app.gateway.llm import LLMGateway

logger = get_logger(__name__)

# CriticAgent 的 LLM prompt（Stage MA）。OpportunityAgent 提出候选后，CriticAgent
# 会针对 evidence ledger 对每个候选进行对抗式审阅，并返回 challenge 与 verdict。
# Orchestrator 根据 verdict 保留、收窄或降低弱 opportunity 的权重；这就是 demo 展示的
# “multi-agent collaboration”（不是原始模型推理过程）。
CRITIC_SYSTEM_PROMPT = """\
You are a rigorous, adversarial reviewer of proposed research opportunities. \
For each candidate, identify weaknesses it must address before it can be \
considered novel and viable.

Challenge categories:
- counter_evidence: the evidence ledger already contains work covering the claim
- overlap: the proposal overlaps too much with existing similar work
- assumption: a stated assumption is unsupported or brittle
- framing: the research question is too broad or ill-defined
- evaluation: the proposed validation cannot falsify the hypothesis

Rules:
- Be specific; reference the evidence roles (supporting / similar / counter / external)
- Verdict per candidate: "keep" (novel and viable), "narrow" (viable after \
tightening focus), or "reject" (not novel or fatally flawed)
- Be conservative: do not invent evidence that is not in the ledger

Output a JSON object, nothing else:
{"reviews": [{"index": 0, "verdict": "keep|narrow|reject", "challenges": ["..."], \
"suggested_narrowing": "..."}, ...]}"""

# 有界 Critic 收窄循环（MA）。当 Critic 将候选标记为 "narrow" 时，Orchestrator 会
# 针对建议的收窄方向执行一次 focused counter-evidence pass，而不是进行无界重综合。
# 结果（发现 obstacle 或方向清晰）会记录在候选上并展示给用户，使 multi-agent 循环
# 保持低成本且可预测。
MA_NARROW_MAX_ITERATIONS = 1
MA_NARROW_COUNTER_TOP_K = 8
MA_NARROW_OBSTACLE_CONFIDENCE = 0.6  # counter evidence at/above this confidence counts as an obstacle




def _make_empty_response(workspace_id: str, query: str, purpose: str) -> RetrievalResponse:
    return RetrievalResponse(
        workspace_id=workspace_id,
        query=query,
        purpose=purpose,
        status="succeeded",
        items=[],
    )


# --------------------------------------------------------------------- 辅助函数


def collect_challenges(critic_reviews: list[dict[str, Any]], *, limit: int = 3) -> list[str]:
    """从 narrow/reject 判定中收集去重后的挑战（W2）。

    作为约束反馈给第二次 synthesis，使收窄后的 opportunity 明确回应 Critic 指出的缺口。
    """
    seen: set[str] = set()
    out: list[str] = []
    for review in critic_reviews:
        if str(review.get("verdict") or "keep") not in {"narrow", "reject"}:
            continue
        for ch in review.get("challenges") or []:
            s = str(ch).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
                if len(out) >= limit:
                    return out
    return out


def apply_reviews(
    candidates: list[dict[str, Any]], critic_reviews: list[dict[str, Any]]
) -> dict[str, int]:
    """附加 critic 审查结果并降低弱候选的权重。

    返回各 verdict 的数量。``reject`` 候选的 confidence 最高降至 0.3，``narrow`` 最高
    降至 0.45，使其作为较弱的 opportunity 展示，但不会被静默丢弃（HITL 仍可处理）。
    """
    verdict_counts = {"keep": 0, "narrow": 0, "reject": 0}
    for review in critic_reviews:
        verdict = str(review.get("verdict") or "keep")
        if verdict not in verdict_counts:
            verdict = "keep"
        verdict_counts[verdict] += 1
        idx = review.get("index")
        idx = int(idx) if isinstance(idx, int) else -1
        if not (0 <= idx < len(candidates)):
            continue
        candidate = candidates[idx]
        candidate["critic_review"] = review
        confidence = float(candidate.get("confidence") or 0.5)
        candidate["confidence"] = min(
            confidence,
            0.3 if verdict == "reject" else (0.45 if verdict == "narrow" else confidence),
        )
    return verdict_counts


def narrowing_obstacle(counter: RetrievalResponse) -> bool:
    """当聚焦后的 counter evidence 已覆盖收窄后的 claim 时返回 True。"""
    for item in counter.items:
        if (
            item.judgement in {"contradicts", "qualifies"}
            and (item.judgement_confidence or 0.0) >= MA_NARROW_OBSTACLE_CONFIDENCE
        ):
            return True
    return False


# --------------------------------------------------------------------- 服务


class CriticService:
    """CriticAgent 编排：审查 → 挑战 → 收窄流程。

    由 ``DiscoverService`` 组合；调用方应通过该 facade 访问，以保持使用
    ``service._critic_*`` 的既有测试正常运行。
    """

    def __init__(
        self,
        db: Session,
        llm: LLMGateway,
        retrieval: RetrievalPort,
        *,
        empty_response=None,
    ) -> None:
        self.db = db
        self.llm = llm
        self.retrieval = retrieval
        self._empty = empty_response or _make_empty_response

    def review(
        self,
        run: DiscoverRun,
        claim_text: str,
        candidates: list[dict[str, Any]],
        supporting: RetrievalResponse,
        similar: RetrievalResponse,
        counter: RetrievalResponse,
    ) -> list[dict[str, Any]]:
        """对提议的候选执行对抗式审查（CriticAgent）。

        返回每个候选的 verdict（keep/narrow/reject）和 challenges，供 Orchestrator
        降低弱 opportunity 的权重或标记它们。LLM 失败时返回 ``[]``；run 保留候选并记录
        critic-failed 步骤，因此流水线不会因 Critic 阻塞。
        """
        if not candidates:
            return []
        briefs = [
            f"[{i}] {str(c.get('title') or '')[:120]} — {str(c.get('problem_statement') or '')[:220]}"
            for i, c in enumerate(candidates)
        ]
        evidence_brief = {
            "supporting": [retrieval_payload(item) for item in supporting.items[:6]],
            "similar": [retrieval_payload(item) for item in similar.items[:6]],
            "counter": [retrieval_payload(item) for item in counter.items[:6]],
        }
        user_prompt = (
            f"RESEARCH QUESTION: {claim_text[:300]}\n\n"
            f"EVIDENCE LEDGER:\n{json.dumps(evidence_brief, ensure_ascii=False)}\n\n"
            f"CANDIDATES:\n" + "\n".join(briefs)
        )
        try:
            resp = self.llm.chat_completion(
                [
                    {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=2000,
                disable_thinking=True,
            )
            accumulate_tokens(run, resp)
            parsed = parse_json(resp.content)
            reviews = parsed.get("reviews") if isinstance(parsed, dict) else None
            if not isinstance(reviews, list):
                logger.warning("discover.critic_bad_shape", raw_preview=(resp.content or "")[:200])
                return []
            out: list[dict[str, Any]] = []
            for review in reviews:
                if not isinstance(review, dict) or not isinstance(review.get("index"), int):
                    continue
                idx = int(review["index"])
                if not (0 <= idx < len(candidates)):
                    continue
                verdict = str(review.get("verdict") or "keep").lower()
                if verdict not in {"keep", "narrow", "reject"}:
                    verdict = "keep"
                out.append(
                    {
                        "index": idx,
                        "verdict": verdict,
                        "challenges": [s for s in review.get("challenges") or [] if isinstance(s, str)],
                        "suggested_narrowing": str(review.get("suggested_narrowing") or ""),
                    }
                )
            return out
        except Exception as exc:
            logger.warning("discover.critic_failed", run_id=run.id, error=str(exc))
            return []

    def narrowing_pass(
        self,
        run: DiscoverRun,
        candidates: list[dict[str, Any]],
        critic_reviews: list[dict[str, Any]],
    ) -> int:
        """对 Critic 标记为 "narrow" 的候选执行一次有界收窄。

        对每个带有收窄建议的 narrow 候选，在收窄后的焦点上执行聚焦反证检索，
        并记录是否发现障碍。候选不会被静默丢弃——结果记录在
        ``candidate["narrowing_pass"]`` 中，供 HITL 查看收窄轨迹。
        返回被收窄的候选数量。
        """
        by_index: dict[int, dict[str, Any]] = {}
        for review in critic_reviews:
            idx = review.get("index")
            if isinstance(idx, int):
                by_index[idx] = review
        narrow = [
            (idx, r)
            for idx, r in by_index.items()
            if r.get("verdict") == "narrow" and r.get("suggested_narrowing") and 0 <= idx < len(candidates)
        ]
        if not narrow:
            return 0
        config = DiscoverConfig.model_validate(run.config or {})
        excluded = (
            {claim_paper}
            if (claim_paper := (run.input_payload or {}).get("claim_paper_id"))
            else set()
        )
        narrowed = 0
        for idx, review in narrow:
            candidate = candidates[idx]
            narrowing = str(review.get("suggested_narrowing") or "").strip()
            base = str(candidate.get("candidate_research_question") or candidate.get("title") or "")
            query = f"{base[:300]} {narrowing[:120]}".strip()
            if not query:
                continue
            try:
                counter = self.retrieval.find_counter_evidence(
                    run.workspace_id,
                    query,
                    MA_NARROW_COUNTER_TOP_K,
                    use_reranker=config.use_reranker,
                    use_judge=config.use_judge,
                    exclude_paper_ids=excluded or None,
                )
            except Exception as exc:
                logger.warning("discover.narrowing_retrieval_failed", run_id=run.id, error=str(exc))
                counter = self._empty(run.workspace_id, query, "counter_evidence")
            obstacle = narrowing_obstacle(counter)
            candidate["narrowing_pass"] = {
                "query": query[:300],
                "counter_candidates": len(counter.items),
                "obstacle": obstacle,
                "outcome": "obstacle_found" if obstacle else "direction_clear",
            }
            if obstacle:
                candidate["confidence"] = min(float(candidate.get("confidence") or 0.5), 0.25)
            narrowed += 1
        return narrowed
