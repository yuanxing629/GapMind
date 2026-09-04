"""Discover Agent 编排与 opportunity 工作流。

service 将耗时的 external/LLM 工作放在 HTTP 事务之外。Run 是持久化的产品对象；Celery
仅作为执行机制。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.domains.agent.models import AgentRun, AgentStep
from app.domains.artifact.models import Artifact
from app.domains.artifact.service import ArtifactService
from app.domains.discover.adapters import (
    ExternalSearchAdapter,
    LLMGatewayAdapter,
    RetrievalAdapter,
    assert_protocol,
)
from app.domains.discover.critic import (
    CriticService,
    apply_reviews,
    collect_challenges,
    narrowing_obstacle,
)
from app.domains.discover.synthesis import (
    SynthesisService,
    fallback_candidate,
    normalize_candidate,
    retrieval_payload,
)
from app.domains.discover.external_retrieval import (
    ExternalRetrievalService,
    external_role,
    normalize_pdf_url,
    title_verified,
)



from app.domains.discover.models import (
    DiscoverExternalCandidate,
    DiscoverRun,
    HumanDecision,
    OpportunityEvidence,
    OpportunityVersion,
    ResearchOpportunity,
    ResearchPlan,
)
from app.domains.discover.opportunity_workflow import OpportunityWorkflow
from app.domains.discover.ports import ExternalSearchPort, LLMGatewayPort, RetrievalPort
from app.domains.discover.schemas import (
    DiscoverConfig,
    DiscoverInput,
    DiscoverRunCreateRequest,
    DiscoverScope,
)
from app.domains.knowledge.models import EvidenceSpan, KnowledgeItem
from app.domains.paper.models import Paper
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem
from app.domains.task.schemas import TaskCreate
from app.domains.task.service import TaskService
from app.domains.timeline.service import TimelineService

logger = get_logger(__name__)

TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}

# W2：每个 DiscoverRun 都记录 prompt version，便于审计。
DISCOVER_PROMPT_VERSION = "discover-v2"

# 外部候选角色判断的 LLM prompt（Stage 3）。
EXTERNAL_ROLE_SYSTEM_PROMPT = """\
You classify whether external research papers serve as counter-evidence for a \
research question.

Categories:
- similar: same research area, closely related approach
- overlap: partially overlapping topic but different focus
- qualifies: adds caveats or limitations that constrain the research question
- contradicts: provides evidence against the research question
- unknown: cannot determine from the metadata alone

Rules:
- Be conservative: use "unknown" if ambiguous
- "contradicts" requires clear opposing evidence, not just a different focus
- Base your judgement on the title and abstract only
- A candidate that merely resembles the question is "similar"; only call it \
"qualifies" or "contradicts" when it explicitly challenges or constrains it

Output a JSON object, nothing else:
{"roles": [{"index": 0, "role": "similar|overlap|qualifies|contradicts|unknown", \
"confidence": 0.0-1.0}, ...]}"""
WAITING_RUN_STATUSES = {"waiting_for_user", "waiting_for_fulltext"}
PIPELINE_PENDING_STATUSES = {"queued", "running", "waiting_for_user"}

# 外部 query 研究轴拆解的 LLM prompt（Stage 3）。单独使用研究问题的长段文字，
# 作为 Semantic Scholar relevance query 的效果较差；LLM 会将其拆解为简洁且术语丰富的
# 搜索 query，覆盖基础方法、重叠工作、反证和评估/批评文献，并以 workspace 提取的
# method/limitation 为上下文，使 query 能够跨研究轴覆盖 workspace 中命名的方法。
EXTERNAL_QUERY_AXIS_SYSTEM_PROMPT = """\
You write effective search queries to find EXTERNAL papers that challenge, \
overlap with, or foundationally support a research question. The papers must \
be relevant to the question but are NOT required to be in the user's workspace.

Rules:
- Write CONCISE keyword-style queries (3-8 words), never full sentences
- Prefer SPECIFIC method names and established concept terms over generic \
topic phrases (e.g. "graph information bottleneck" or "invariant risk \
minimization", not just "interpretable GNN")
- Turn at least 2 of the workspace's abbreviated method names into concrete \
queries using their FULL names, so the search finds the method's paper plus \
its variants and critiques
- Cover distinct angles: foundational methods, overlapping prior work, \
counter-evidence / critiques, evaluation benchmarks, and the domain axis \
(e.g. distribution shift) when present
- Do not quote the workspace paper titles verbatim
- Never repeat the same idea in two queries

Also choose up to 2 workspace method names whose papers you want surfaced
PRECISELY (these are searched by exact title, so give the full descriptive
name — expand abbreviations). Prefer methods that are foundational or likely
to have counter-evidence / variants. Do not list the same method twice.

Examples of good queries:
- "graph information bottleneck"
- "invariant risk minimization out-of-distribution"
- "saliency maps sanity checks"
- "explanation robustness adversarial perturbations"
- "graph rationalization environment augmentation"

Output a JSON object, nothing else:
{"queries": ["...", "...", "..."], "exact_lookups": ["Method Full Name", "...", "..."]}"""

# Stage 3 外部 query 构建预算。少量聚焦 query 比直接使用 claim 原文能覆盖更多角度，
# 同时保持 S2 API 调用和 LLM 角色判断 batch 有界。LLM 拆解出的研究轴 query 是价值最高
# 的外部搜索 key，因此优先级高于原始 workspace 信号和通用关键词。
EXTERNAL_QUERY_MAX_TOTAL = 8  # kept for compatibility with older imports
EXTERNAL_QUERY_AXIS_COUNT = 5  # kept for compatibility with older imports
EXTERNAL_QUERY_MAX_EXACT_LOOKUPS = 2  # kept for compatibility with older imports
EXTERNAL_QUERY_SIGNAL_TYPES = ("method", "claim", "task", "limitation")
EXTERNAL_QUERY_MIN_CONFIDENCE = 0.3  # skip low-confidence extracted signals
EXTERNAL_QUERY_MAX_KEYWORDS = 2  # generic user keywords are lowest priority
# Architecture component 不是命名的研究贡献，因此不适合作为外部搜索 key；降低其优先级，
# 让真正的方法名称优先。
EXTERNAL_METHOD_COMPONENT_TOKENS = {
    "pool",
    "module",
    "layer",
    "encoder",
    "decoder",
    "aggregation",
    "step",
    "fourier",
    "regularization",
    "block",
}

WAITING_RUN_STATUSES = {"waiting_for_user", "waiting_for_fulltext"}
# Domain exception class 放在独立模块中，以便子模块（以及测试）导入时无需加载整个 service。
from app.domains.discover.exceptions import (  # noqa: E402
    DiscoverGateError,
    DiscoverInputError,
    DiscoverRunDeletionConflict,
    DiscoverRunCancelled,
    DiscoverRunNotFoundError,
    InvalidOpportunityTransition,
    OpportunityNotFoundError,
    OpportunityVersionConflict,
)


class DiscoverService(OpportunityWorkflow):
    def __init__(
        self,
        db: Session,
        *,
        retrieval: RetrievalPort | None = None,
        external_search: ExternalSearchPort | None = None,
        llm: LLMGatewayPort | None = None,
    ) -> None:
        self.db = db
        self.timeline = TimelineService(db)

# 通过 Protocol port 绑定跨 domain 协作者（见 ``ports.py``）。测试可以注入兼容 Protocol
# 的 fake，在没有 Milvus / LLM / S2 的情况下验证 orchestration。
        self.retrieval: RetrievalPort = retrieval or RetrievalAdapter(db)
        self.external_search: ExternalSearchPort = external_search or ExternalSearchAdapter()
        self.llm: LLMGatewayPort = llm or LLMGatewayAdapter()

# 廉价的运行时完整性检查：如果自定义 binding 缺少 orchestrator 调用的方法，则立即报错。
        assert_protocol(self.retrieval, RetrievalPort)
        assert_protocol(self.external_search, ExternalSearchPort)
        assert_protocol(self.llm, LLMGatewayPort)
        self.critic = CriticService(db, llm=self.llm, retrieval=self.retrieval, empty_response=self._empty_response)
        self.synthesis = SynthesisService(db, llm=self.llm)
        self.external = ExternalRetrievalService(db, llm=self.llm, external_search=self.external_search, service=self)

# ---------------------------------------------------------------- 运行记录
    def create_run(
        self,
        workspace_id: str,
        request: DiscoverRunCreateRequest,
        *,
        trigger_type: str = "topic",
        parent_run_id: str | None = None,
        actor: str = "user",
    ) -> tuple[DiscoverRun, str | None]:
        claim = self._resolve_claim(workspace_id, request.input.claim_item_id)
        topic = (request.input.topic or "").strip() or (self._claim_text(claim) if claim else "")
        if not topic:
            raise DiscoverInputError("a topic or a valid claim is required")
        self._validate_papers(workspace_id, request.input.paper_ids)

        task = TaskService(self.db).create(
            TaskCreate(
                workspace_id=workspace_id,
                task_type="discover_agent",
                payload={"kind": "discover", "status": "queued"},
            )
        )
        run = DiscoverRun(
            id=str(uuid4()),
            workspace_id=workspace_id,
            task_id=task.id,
            parent_run_id=parent_run_id,
            trigger_type=trigger_type,
            input_topic=topic,
            input_claim_item_id=claim.id if claim else None,
            input_payload=request.input.model_dump(mode="json"),
            scope=request.scope.model_dump(mode="json"),
            config=request.config.model_dump(mode="json"),
            status="queued",
            stage="preflight",
            progress=0.0,
            verification_status="not_started",
            model_provider="remote",
            model_name=settings.remote_model or "remote",
            model_parameters={"temperature": 0.1, "max_tokens": 2200},
            prompt_version=DISCOVER_PROMPT_VERSION,
            corpus_version=self._corpus_snapshot(workspace_id),
            stage_summaries={},
        )
        self.db.add(run)
        self.db.flush()
        task.payload = {**(task.payload or {}), "run_id": run.id}
        self.db.commit()
        self.db.refresh(run)
        self.timeline.record(
            workspace_id=workspace_id,
            event_type="discover.run_created",
            subject_type="discover_run",
            subject_id=run.id,
            actor=actor,
            payload={"run_id": run.id, "task_id": task.id, "trigger_type": trigger_type},
        )
        return run, task.id

    def list_runs(
        self, workspace_id: str, *, status_filter: str | None, limit: int, offset: int
    ) -> tuple[list[DiscoverRun], int]:
        base = select(DiscoverRun).where(
            DiscoverRun.workspace_id == workspace_id,
            DiscoverRun.deleted_at.is_(None),
        )
        if status_filter:
            base = base.where(DiscoverRun.status == status_filter)
        items = list(
            self.db.execute(
                base.order_by(DiscoverRun.created_at.desc()).limit(limit).offset(offset)
            ).scalars()
        )
        total = int(
            self.db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
        )
        return items, total

    def get_run(self, workspace_id: str, run_id: str) -> DiscoverRun:
        run = self.db.get(DiscoverRun, run_id)
        if run is None or run.workspace_id != workspace_id or run.deleted_at is not None:
            raise DiscoverRunNotFoundError(run_id)
        return run

    def run_detail(self, workspace_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_run(workspace_id, run_id)
        candidates = list(self.db.execute(select(DiscoverExternalCandidate).where(DiscoverExternalCandidate.discover_run_id == run.id).order_by(DiscoverExternalCandidate.rank)).scalars())
        opportunities = list(self.db.execute(select(ResearchOpportunity).where(ResearchOpportunity.discover_run_id == run.id, ResearchOpportunity.is_deleted.is_(False)).order_by(ResearchOpportunity.created_at)).scalars())
        return {
            "run": run,
            "external_candidates": candidates,
            "opportunities": opportunities,
            "agent_steps": self._run_agent_steps(run),
        }

    def _run_agent_steps(self, run: DiscoverRun) -> list[AgentStep]:
        """该 Discover run 记录的多 Agent 交接信息（运行前为空）。"""
        if not run.task_id:
            return []
        agent_run = self.db.scalar(
            select(AgentRun).where(
                AgentRun.task_id == run.task_id,
                AgentRun.agent_type == "discover",
            )
        )
        if agent_run is None:
            return []
        return list(
            self.db.execute(
                select(AgentStep).where(AgentStep.run_id == agent_run.id).order_by(AgentStep.sequence)
            ).scalars()
        )

    def cancel_run(self, workspace_id: str, run_id: str) -> DiscoverRun:
        run = self.get_run(workspace_id, run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            raise InvalidOpportunityTransition(f"Run is already {run.status}")
        if run.task_id:
            TaskService(self.db).request_cancel(run.task_id)
        run.status = "cancelled"
        run.finished_at = datetime.now(timezone.utc)
        run.stage = "cancelled"
        self.db.commit()
        self.timeline.record(
            workspace_id=workspace_id,
            event_type="discover.run_cancelled",
            subject_type="discover_run",
            subject_id=run.id,
            payload={"run_id": run.id},
        )
        return run

    def delete_run(self, workspace_id: str, run_id: str, *, actor: str = "user") -> None:
        """隐藏已完成的 Discover Run，但不删除其研究数据。"""
        run = self.get_run(workspace_id, run_id)
        if run.status not in TERMINAL_RUN_STATUSES:
            raise DiscoverRunDeletionConflict(
                "Only completed, failed, or cancelled Discover runs can be deleted; cancel the active run first."
            )
        run.deleted_at = datetime.now(timezone.utc)
        run.deleted_by = actor
        self.db.commit()
        self.timeline.record(
            workspace_id=workspace_id,
            event_type="discover.run_deleted",
            subject_type="discover_run",
            subject_id=run.id,
            actor=actor,
            payload={"run_id": run.id, "preserved_outputs": True},
        )

    def select_external(
        self, workspace_id: str, run_id: str, candidate_ids: list[str]
    ) -> DiscoverRun:
        run = self.get_run(workspace_id, run_id)
        if run.status != "waiting_for_user" or run.stage != "external_selection":
            raise DiscoverInputError("Run is not waiting for external paper selection")
        rows = list(
            self.db.execute(
                select(DiscoverExternalCandidate).where(
                    DiscoverExternalCandidate.discover_run_id == run.id,
                    DiscoverExternalCandidate.id.in_(candidate_ids),
                )
            ).scalars()
        )
        if len(rows) != len(set(candidate_ids)):
            raise DiscoverInputError("one or more external candidates do not belong to this run")
        protected_statuses = {"selected", "imported_pending_parse", "verified"}
        if any(row.verification_status in protected_statuses for row in rows):
            raise DiscoverInputError(
                "one or more external candidates are already selected or verified"
            )
        for row in rows:
            row.verification_status = "selected"
        run.status = "queued"
        run.stage = "fulltext_verification"
        run.progress = max(run.progress, 0.65)
        run.verification_status = "in_progress"
        run.stage_summaries = {
            **(run.stage_summaries or {}),
            "external_selection": {"selected": len(rows), "status": "queued"},
        }
        self.db.commit()
        if run.task_id:
            try:
                TaskService(self.db).resume_from_user(
                    run.task_id, decision={"candidate_ids": candidate_ids}
                )
            except Exception:
                pass
        return run

    def skip_external_selection(
        self,
        workspace_id: str,
        run_id: str,
        *,
        actor: str = "user",
    ) -> DiscoverRun:
        run = self.get_run(workspace_id, run_id)
        if run.status != "waiting_for_user" or run.stage != "external_selection":
            raise DiscoverInputError("Run is not waiting for external paper selection")
        run.status = "queued"
        run.stage = "synthesis"
        run.progress = max(run.progress, 0.62)
        run.verification_status = "incomplete"
        run.stage_summaries = {
            **(run.stage_summaries or {}),
            "external_selection": {
                "status": "skipped",
                "reason": "user_skipped",
                "selected": 0,
                "actor": actor,
                "skipped_at": datetime.now(timezone.utc).isoformat(),
            },
        }
        self.db.commit()
        if run.task_id:
            TaskService(self.db).resume_from_user(
                run.task_id,
                decision={"action": "skip_external_selection"},
            )
        self.timeline.record(
            workspace_id=workspace_id,
            event_type="discover.external_selection_skipped",
            subject_type="discover_run",
            subject_id=run.id,
            actor=actor,
            payload={"run_id": run.id, "reason": "user_skipped"},
        )
        return run

    @staticmethod
    def _external_selection_skipped(run: DiscoverRun) -> bool:
        summary = (run.stage_summaries or {}).get("external_selection") or {}
        return summary.get("status") == "skipped"

# ---------------------------------------------------------- 外部检索（MA-1）
# 委托给 ExternalRetrievalService（app.domains.discover.external_retrieval）。
    def _external_candidate_state(self, run):
        return self.external._external_candidate_state(run)

    def _wait_for_fulltext(self, run, state):
        return self.external._wait_for_fulltext(run, state)

    def _paper_pipeline_state(self, paper_id):
        return self.external._paper_pipeline_state(paper_id)

    def _corpus_snapshot(self, workspace_id: str) -> str:
        """用于 run 审计的简短语料指纹（W2）：论文数和知识数。

        使下游审计者只根据 run 行就能知道“该 run 由哪份语料产生”，无需在审查时重新
        查询数量。
        """
        papers = int(
            self.db.execute(
                select(func.count()).select_from(Paper).where(
                    Paper.workspace_id == workspace_id, Paper.is_deleted.is_(False)
                )
            ).scalar()
            or 0
        )
        knowledge = int(
            self.db.execute(
                select(func.count()).select_from(KnowledgeItem).where(
                    KnowledgeItem.workspace_id == workspace_id,
                    KnowledgeItem.is_deleted.is_(False),
                )
            ).scalar()
            or 0
        )
        return f"workspace-v1-{papers}p-{knowledge}k"

# -------------------------------------------------------------- worker：任务执行
    def execute_run(self, run_id: str) -> dict[str, Any]:
        run = self.db.get(DiscoverRun, run_id)
        if run is None:
            raise DiscoverRunNotFoundError(run_id)
        if run.status in TERMINAL_RUN_STATUSES:
            return {"run_id": run.id, "status": run.status, "idempotent": True}
        task_service = TaskService(self.db)
        if self._cancelled(run):
            return {"run_id": run.id, "status": "cancelled", "idempotent": True}
        if run.task_id:
            task = task_service.get(run.task_id)
            if task.status == "queued":
                task_service.transition(task.id, "running")
            elif task.status in {"cancel_requested", "cancelled"}:
                return self._cancelled_result(run)
        run.status = "running"
        run.started_at = run.started_at or datetime.now(timezone.utc)
# 刷新 corpus fingerprint——run 在队列中等待时 workspace 可能已经发生变化。
        run.corpus_version = self._corpus_snapshot(run.workspace_id)
        self._stage(run, "preflight", 0.05)

        claim = self._resolve_claim(run.workspace_id, run.input_claim_item_id)
        claim_text = self._claim_text(claim) if claim else (run.input_topic or "")
        if not claim_text.strip():
            return self._fail_run(
                run, "discover_preflight_failed", "No usable topic or claim was provided"
            )

        agent_run = self._discover_agent_run(run)
        self._sync_agent_run(agent_run, run)
        self._agent_step(
            agent_run,
            "planner",
            "completed",
            "Decomposed the research question and planned evidence gathering.",
            {"research_question": claim_text[:300], "claim_item_id": run.input_claim_item_id},
        )

        config = DiscoverConfig.model_validate(run.config or {})
        self._checkpoint(run)
        similar = self._workspace_similar(run, claim, claim_text, config)
        self._stage(run, "workspace_retrieval", 0.28, {"similar_work": len(similar.items)})
        self._checkpoint(run)
        self._stage(
            run, "similar_work", 0.34, {"items": len(similar.items), "status": similar.status}
        )
        counter = self._workspace_counter(run, claim, claim_text, config)
        self._stage(
            run,
            "counter_evidence",
            0.42,
            {
                **self._counter_summary(counter),
                "status": counter.status,
            },
        )
        self._agent_step(
            agent_run,
            "evidence",
            "completed",
            "Retrieved workspace supporting, similar-work, and counter-evidence.",
            {
                "similar": len(similar.items),
                "counter": len(counter.items),
                "workspace_status": similar.status,
            },
        )

        external_queries, exact_lookups = self._external_query_plan(run, claim_text)
        external = self._external_verify(run, external_queries, exact_lookups)
        external_summary = (run.stage_summaries or {}).get("external_search")
        if not isinstance(external_summary, dict):
            external_summary = {}
        self._stage(
            run,
            "external_search",
            0.58,
            {**external_summary, "external_candidates": external},
        )
        self._agent_step(
            agent_run,
            "external_novelty",
            "completed",
            "Searched external literature and classified candidate roles.",
            {"candidates": external, "query_count": len(external_queries)},
        )
        self._checkpoint(run)
        candidate_state = self._external_candidate_state(run)
        selected = candidate_state["selected"]
        pending = candidate_state["pending"]
        verified = candidate_state["verified"]
        failed = candidate_state["failed"]
        if (
            external
            and not self._external_selection_skipped(run)
            and not selected
            and not pending
            and not verified
            and not failed
        ):
            run.status = "waiting_for_user"
            run.stage = "external_selection"
            run.progress = 0.62
            run.verification_status = "incomplete"
            run.stage_summaries = {
                **(run.stage_summaries or {}),
                "external_selection": {"status": "waiting_for_user", "candidate_count": external},
            }
            self.db.commit()
            if run.task_id:
                try:
                    task_service.transition(run.task_id, "waiting_for_user", progress=run.progress)
                except Exception:
                    pass
            self.timeline.record(workspace_id=run.workspace_id, event_type="discover.external_input_requested", subject_type="discover_run", subject_id=run.id, actor="agent", payload={"run_id": run.id, "candidate_count": external})
            self._sync_agent_run(agent_run, run)
            self._agent_step(
                agent_run,
                "external_selection",
                "waiting",
                "Waiting for the user to select external candidates for full-text verification.",
                {"candidate_count": external},
            )
            return {"run_id": run.id, "status": run.status, "waiting_for_user": True}
        if selected:
            self._import_selected_candidates(run)
            candidate_state = self._external_candidate_state(run)
            if candidate_state["pending"]:
                return self._wait_for_fulltext(run, candidate_state)
            if not candidate_state["verified"] and candidate_state["failed"]:
                return self._wait_for_fulltext(run, candidate_state)
            self._stage(
                run,
                "fulltext_verification",
                0.68,
                {"selected": selected, "verified": candidate_state["verified"]},
            )
        elif pending:
            return self._wait_for_fulltext(run, candidate_state)
        elif verified:
# W1：恢复运行时完成 full-text verification——根据导入论文的全文优化 metadata-level role。
            judged = self._judge_external_fulltext_roles(run, claim_text)
            self._stage(
                run,
                "fulltext_verification",
                0.70,
                {"selected": selected, "verified": verified, "fulltext_roles_judged": judged},
            )

        self._checkpoint(run)
        supporting = self._workspace_supporting(run, claim, claim_text, config)
        external_fulltext = self._external_fulltext(run, supporting)
        preliminary_gate = self._evidence_gate(
            run,
            candidate=None,
            supporting=supporting,
            counter=counter,
        )
        self._stage(
            run, "synthesis", 0.76, {"status": "running", "preliminary_gate": preliminary_gate}
        )
        candidates = self._synthesize_candidates(
            run,
            claim_text,
            supporting,
            similar,
            counter,
            external_fulltext,
            preliminary_gate,
            config.max_opportunities,
        )
        self._agent_step(
            agent_run,
            "opportunity",
            "completed",
            f"Synthesized {len(candidates)} candidate opportunities from workspace and external evidence.",
            {"candidate_count": len(candidates)},
        )
        self._checkpoint(run)

# CriticAgent：对候选进行对抗式审阅。Verdict 仅供参考——弱候选会降低权重但不会静默丢弃，
# Critic 失败也不能阻塞 pipeline。
        critic_reviews = self._critic_review(
            run,
            claim_text,
            candidates,
            supporting,
            similar,
            counter,
        )
        if critic_reviews:
            verdict_counts = self._apply_critic_reviews(candidates, critic_reviews)
            self._agent_step(
                agent_run,
                "critic",
                "completed",
                "Critic reviewed candidates against the evidence ledger.",
                {"reviews": critic_reviews, "verdicts": verdict_counts},
            )
# W2：将 critic challenge 注入第二次 synthesis，使优化后的 opportunity 明确回应 critic 的 gap。
            challenges = self._critic_challenges(critic_reviews)
            if challenges:
                refined = self._synthesize_candidates(
                    run,
                    claim_text,
                    supporting,
                    similar,
                    counter,
                    external_fulltext,
                    preliminary_gate,
                    config.max_opportunities,
                    critic_feedback=challenges,
                )
                existing_titles = {c["title"] for c in candidates}
                for cand in refined:
                    cand["critic_refined"] = True
                    if cand["title"] not in existing_titles:
                        candidates.append(cand)
                        existing_titles.add(cand["title"])
                self._agent_step(
                    agent_run,
                    "critic",
                    "completed",
                    f"Re-synthesized opportunities addressing {len(challenges)} critic challenge(s).",
                    {"challenges": challenges, "refined": sum(1 for c in candidates if c.get("critic_refined"))},
                )
# Orchestrator 收窄循环（有界）：对标记为 "narrow" 的候选，针对建议方向执行 focused
# 反证检索阶段。
            narrowed = self._narrowing_pass(run, candidates, critic_reviews)
            if narrowed:
                self._agent_step(
                    agent_run,
                    "narrowing",
                    "completed",
                    f"Ran a focused counter-evidence pass to narrow {narrowed} candidate(s).",
                    {"narrowed": narrowed},
                )
        else:
            self._agent_step(
                agent_run,
                "critic",
                "skipped",
                "Critic review unavailable; candidates kept as synthesized.",
            )
        self._checkpoint(run)

        created, final_gates = self._persist_candidates(
            run,
            claim,
            claim_text,
            supporting,
            similar,
            counter,
            external_fulltext,
            candidates,
        )
        self._agent_step(
            agent_run,
            "gate",
            "completed",
            "Applied the evidence gate and persisted opportunities.",
            {
                "opportunities": len(created),
                "verified": any(gate["verified"] for gate in final_gates),
                "needs_more_evidence": sum(not gate["verified"] for gate in final_gates),
            },
        )
        self._checkpoint(run)
        finished_at = datetime.now(timezone.utc)
        verification_status = (
            "complete" if any(gate["verified"] for gate in final_gates) else "incomplete"
        )
        saved_summary = {"opportunities": len(created), "gates": final_gates}
# synthesis 或 persistence 执行期间可能收到取消请求。使用条件 UPDATE，确保过期 worker
# 永远不能用 succeeded 覆盖用户的 cancelled 状态。
        result = self.db.execute(
            update(DiscoverRun)
            .where(DiscoverRun.id == run.id, DiscoverRun.status != "cancelled")
            .values(
                status="succeeded",
                stage="saved",
                progress=1.0,
                verification_status=verification_status,
                finished_at=finished_at,
                stage_summaries={
                    **(run.stage_summaries or {}),
                    "saved": saved_summary,
                },
            )
        )
        if result.rowcount != 1:
            self.db.rollback()
            cancelled = self.db.get(DiscoverRun, run.id)
            if cancelled is not None and cancelled.status == "cancelled":
                return self._cancelled_result(cancelled)
            raise DiscoverRunCancelled(run.id)
        self.db.commit()
        self.db.refresh(run)
        if run.task_id:
            try:
                task_service.transition(
                    run.task_id,
                    "succeeded",
                    progress=1.0,
                    result={"run_id": run.id, "opportunity_ids": [item.id for item in created]},
                )
            except Exception:
                pass
        self.timeline.record(workspace_id=run.workspace_id, event_type="discover.run_completed", subject_type="discover_run", subject_id=run.id, actor="agent", payload={"run_id": run.id, "opportunities": len(created), "verification_status": run.verification_status})
        if agent_run is not None:
            agent_run.status = "succeeded"
            self._agent_step(
                agent_run,
                "complete",
                "completed",
                f"Discovery run finished with {len(created)} opportunities.",
                {"verification_status": run.verification_status},
            )
        return {"run_id": run.id, "status": run.status, "opportunity_ids": [item.id for item in created]}

    def _checkpoint(self, run: DiscoverRun) -> None:
        """刷新 run，并在用户取消后停止该 worker。"""
        self.db.refresh(run)
        if self._cancelled(run):
            raise DiscoverRunCancelled(run.id)

    @staticmethod
    def _cancelled(run: DiscoverRun) -> bool:
        return run.status == "cancelled"

    def _cancelled_result(self, run: DiscoverRun) -> dict[str, Any]:
        run.status = "cancelled"
        run.stage = "cancelled"
        run.finished_at = run.finished_at or datetime.now(timezone.utc)
        self.db.commit()
        return {"run_id": run.id, "status": "cancelled", "idempotent": True}

# -------------------------------------------------------------- agent 可观测性
    def _discover_agent_run(self, run: DiscoverRun) -> AgentRun | None:
        """查找或创建镜像该 Discover run 编排过程的 AgentRun。

        复用 workspace 的 AgentRun/AgentStep protocol（agent domain），使 Discover 流程
        以可审计的 agent handoff 形式呈现，与多智能体方向保持一致。该记录以 Discover
        run 的 ``task_id`` 为键，因此可以跨 worker 恢复（external-selection 和 fulltext
        暂停）。当 run 没有关联 task 时返回 ``None``（例如测试直接构造 run）。
        """
        if run.task_id:
            existing = self.db.scalar(
                select(AgentRun).where(
                    AgentRun.task_id == run.task_id,
                    AgentRun.agent_type == "discover",
                )
            )
            if existing is not None:
                return existing
        agent_run = AgentRun(
            workspace_id=run.workspace_id,
            task_id=run.task_id,
            agent_type="discover",
            status=run.status,
            current_stage=run.stage,
            progress=run.progress,
            input_payload={"discover_run_id": run.id, "trigger": run.trigger_type},
            context_snapshot={"input_topic": run.input_topic, "claim_item_id": run.input_claim_item_id},
            requires_confirmation=False,
        )
        self.db.add(agent_run)
        self.db.flush()
        return agent_run

    def _agent_step(self, agent_run: AgentRun | None, stage: str, status: str, summary: str, details: dict[str, Any] | None = None) -> None:
        """向 discover AgentRun 追加 AgentStep（跨恢复操作保持幂等）。"""
        if agent_run is None:
            return
        max_seq = int(self.db.scalar(select(func.max(AgentStep.sequence)).where(AgentStep.run_id == agent_run.id)) or 0)
        self.db.add(
            AgentStep(
                run_id=agent_run.id,
                sequence=int(max_seq) + 1,
                stage=stage,
                status=status,
                summary=summary,
                details=details or {},
            )
        )
        agent_run.current_stage = stage
        if agent_run.status not in {"succeeded", "failed", "cancelled"}:
            agent_run.status = "running"
        self.db.commit()

    def _sync_agent_run(self, agent_run: AgentRun | None, run: DiscoverRun) -> None:
        if agent_run is None:
            return
        agent_run.status = run.status
        agent_run.current_stage = run.stage
        agent_run.progress = run.progress
        self.db.commit()

# -------------------------------------------------------------- CriticAgent（MA-1）：批评代理
# 委托给 CriticService（app.domains.discover.critic）。保留薄封装，以便现有调用方
#（orchestrator + tests）继续工作。
    def _critic_review(self, run, claim_text, candidates, supporting, similar, counter):
        return self.critic.review(run, claim_text, candidates, supporting, similar, counter)

    @staticmethod
    def _apply_critic_reviews(candidates, critic_reviews):
        return apply_reviews(candidates, critic_reviews)

    @staticmethod
    def _critic_challenges(critic_reviews, *, limit=3):
        return collect_challenges(critic_reviews, limit=limit)

    def _narrowing_pass(self, run, candidates, critic_reviews):
        return self.critic.narrowing_pass(run, candidates, critic_reviews)

    @staticmethod
    def _narrowing_obstacle(counter):
        return narrowing_obstacle(counter)

    def _stage(self, run: DiscoverRun, stage: str, progress: float, summary: dict[str, Any] | None = None) -> None:
        self.db.refresh(run)
        if self._cancelled(run):
            raise DiscoverRunCancelled(run.id)
        run.stage = stage
        run.progress = progress
        run.stage_summaries = {
            **(run.stage_summaries or {}),
            stage: summary or {"status": "succeeded"},
        }
        self.db.commit()
        self.timeline.record(
            workspace_id=run.workspace_id,
            event_type="discover.stage_completed",
            subject_type="discover_run",
            subject_id=run.id,
            actor="agent",
            payload={
                "run_id": run.id,
                "stage": stage,
                "progress": progress,
                "summary": summary or {},
            },
        )

    def _fail_run(self, run: DiscoverRun, code: str, message: str) -> dict[str, Any]:
        run.status = "failed"
        run.stage = "failed"
        run.error_code = code
        run.error_message = message
        run.finished_at = datetime.now(timezone.utc)
        self.db.commit()
        if run.task_id:
            try:
                TaskService(self.db).transition(run.task_id, "failed", error=message)
            except Exception:
                pass
        self.timeline.record(
            workspace_id=run.workspace_id,
            event_type="discover.run_failed",
            subject_type="discover_run",
            subject_id=run.id,
            payload={"run_id": run.id, "error_code": code, "message": message},
        )
        return {"run_id": run.id, "status": "failed", "error_code": code}

# ----------------------------------------------------------- 检索
    def _workspace_similar(
        self, run: DiscoverRun, claim: KnowledgeItem | None, text: str, config: DiscoverConfig
    ) -> RetrievalResponse:
        paper_id = claim.paper_id if claim else None
        if paper_id:
            return self.retrieval.find_similar_work(
                run.workspace_id,
                paper_id,
                config.top_k,
                use_reranker=config.use_reranker,
                exclude_paper_ids={paper_id},
            )
        return self.retrieval.semantic_search(
            run.workspace_id, text, config.top_k, use_reranker=config.use_reranker
        )

    def _workspace_counter(
        self, run: DiscoverRun, claim: KnowledgeItem | None, text: str, config: DiscoverConfig
    ) -> RetrievalResponse:
        if not config.include_counter_evidence:
            return self._empty_response(run.workspace_id, text, "counter_evidence")
        excluded = {claim.paper_id} if claim and claim.paper_id else set()
        return self.retrieval.find_counter_evidence(
            run.workspace_id,
            text,
            config.top_k,
            use_reranker=config.use_reranker,
            use_judge=config.use_judge,
            exclude_paper_ids=excluded,
        )

    def _external_verify(self, run, queries, exact_lookups=None):
        return self.external._external_verify(run, queries, exact_lookups)

    def _build_external_queries(self, run, primary):
        return self.external._build_external_queries(run, primary)

    def _external_query_plan(self, run, primary):
        return self.external._external_query_plan(run, primary)

    def _axis_queries_from_llm(self, run, primary):
        return self.external._axis_queries_from_llm(run, primary)

    def _external_query_signal_texts(self, workspace_id, *, max_methods=24, max_limitations=6, max_claims=6):
        return self.external._external_query_signal_texts(workspace_id, max_methods=max_methods, max_limitations=max_limitations, max_claims=max_claims)

    def _external_method_full_names(self, workspace_id, *, max_names=40):
        return self.external._external_method_full_names(workspace_id, max_names=max_names)

    def _external_query_signal_items(self, workspace_id, types=None):
        return self.external._external_query_signal_items(workspace_id, types)

    def _external_query_text(self, item):
        return self.external._external_query_text(item)

    def _import_selected_candidates(self, run):
        return self.external._import_selected_candidates(run)

    def _ensure_paper_pipeline(self, workspace_id, paper_id):
        return self.external._ensure_paper_pipeline(workspace_id, paper_id)

    @staticmethod
    def _normalize_pdf_url(url):
        return normalize_pdf_url(url)

    @staticmethod
    def _external_role(query, item):
        return external_role(query, item)

    @staticmethod
    def _title_verified(name, title):
        return title_verified(name, title)

    def _judge_external_roles(self, run, query, candidates):
        return self.external._judge_external_roles(run, query, candidates)

    def _judge_external_fulltext_roles(self, run, query):
        return self.external._judge_external_fulltext_roles(run, query)

    def _read_paper_text(self, paper):
        return self.external._read_paper_text(paper)

    def _workspace_supporting(
        self,
        run: DiscoverRun,
        claim: KnowledgeItem | None,
        text: str,
        config: DiscoverConfig,
    ) -> RetrievalResponse:
        excluded = {claim.paper_id} if claim and claim.paper_id else set()
        response = self.retrieval.semantic_search(
            run.workspace_id, text, config.top_k * 3, use_reranker=config.use_reranker
        )
        return self._filter_supporting_response(run, response, excluded)

    def _candidate_supporting(
        self,
        run: DiscoverRun,
        claim: KnowledgeItem | None,
        candidate: dict[str, Any],
        config: DiscoverConfig,
    ) -> RetrievalResponse:
        """为具体合成的 opportunity 检索证据。

        初始 topic 检索仅用于构建上下文。Final Gate 的证据必须针对候选的 problem 和
        hypothesis 重新检索，避免宽泛的主题匹配被提升为 supporting evidence。
        """
        query = " ".join(
            str(candidate.get(key) or "")
            for key in (
                "problem_statement",
                "candidate_hypothesis",
                "why_existing_work_is_insufficient",
            )
        ).strip()
        excluded = {claim.paper_id} if claim and claim.paper_id else set()
        response = self.retrieval.semantic_search(
            run.workspace_id,
            query[:3000] or (run.input_topic or ""),
            config.top_k * 3,
            use_reranker=config.use_reranker,
        )
        return self._filter_supporting_response(run, response, excluded)

    def _filter_supporting_response(
        self,
        run: DiscoverRun,
        response: RetrievalResponse,
        excluded: set[str],
    ) -> RetrievalResponse:
        if response.status == "failed":
            return response.model_copy(update={"purpose": "supporting_evidence"})
        imported_ids = {
            row.imported_paper_id
            for row in self.db.execute(
                select(DiscoverExternalCandidate).where(
                    DiscoverExternalCandidate.discover_run_id == run.id,
                    DiscoverExternalCandidate.verification_status == "verified",
                    DiscoverExternalCandidate.imported_paper_id.is_not(None),
                )
            ).scalars()
            if row.imported_paper_id
        }
        items: list[RetrievalResultItem] = []
        seen_papers: set[str] = set()
        for item in response.items:
            if not item.paper_id or item.paper_id in excluded or item.paper_id in seen_papers:
                continue
            span = self._find_evidence_span(item, run.workspace_id)
            if (
                item.evidence_level != "full_text"
                or span is None
                or span.relation != "supports"
                or not self._has_valid_evidence_anchor(span, item.text)
            ):
                continue
            item.judgement = "supports"
            item.judgement_confidence = max(item.judgement_confidence, span.confidence)
            item.source_scope = "external" if item.paper_id in imported_ids else "workspace"
            items.append(item)
            seen_papers.add(item.paper_id)
        return response.model_copy(
            update={
                "purpose": "supporting_evidence",
                "items": items,
                "total": len(items),
                "filters_applied": {
                    **(response.filters_applied or {}),
                    "excluded_paper_ids": sorted(excluded),
                    "relation": "supports",
                    "requires_evidence_span": True,
                },
            }
        )

    @staticmethod
    def _counter_summary(response: RetrievalResponse) -> dict[str, Any]:
        judgements = [item.judgement for item in response.items]
        found = [value for value in judgements if value in {"contradicts", "qualifies"}]
        if response.status == "failed":
            outcome = "retrieval_failed"
        elif response.status == "degraded":
            outcome = "judge_degraded_or_failed"
        elif found:
            outcome = "found"
        else:
            outcome = "searched_no_counter_evidence"
        return {
            "outcome": outcome,
            "found": len(found),
            "contradicts": judgements.count("contradicts"),
            "qualifies": judgements.count("qualifies"),
            "items": len(response.items),
        }

    def _external_fulltext(
        self, run: DiscoverRun, supporting: RetrievalResponse
    ) -> RetrievalResponse:
        items = [item for item in supporting.items if item.source_scope == "external"]
        return supporting.model_copy(
            update={"purpose": "external_full_text", "items": items, "total": len(items)}
        )

    def _evidence_gate(
        self,
        run: DiscoverRun,
        *,
        candidate: dict[str, Any] | None,
        supporting: RetrievalResponse,
        counter: RetrievalResponse,
    ) -> dict[str, Any]:
        """仅评估有明确文本范围支持的 supporting evidence。

        Similar Work、Counter Evidence、元数据快照和重复 chunk 会有意排除在该集合之外。
        """
# ``supporting`` 已由具体 proposal 的 ``_candidate_supporting`` 生成。这里不要再应用一次
# lexical-overlap gate：proposal 使用中文生成，而论文摘录经常仍是英文，ASCII token 交集
# 会错误丢弃有效的跨语言 semantic retrieval 命中。
        candidate_items = supporting.items
        valid: list[RetrievalResultItem] = []
        seen_papers: set[str] = set()
        for item in candidate_items:
            if item.paper_id in seen_papers or item.evidence_level != "full_text":
                continue
            span = self._find_evidence_span(item, run.workspace_id)
            if (
                span is None
                or span.relation != "supports"
                or not self._has_valid_evidence_anchor(span, item.text)
            ):
                continue
            if item.judgement != "supports":
                continue
            valid.append(item)
            seen_papers.add(item.paper_id or "")

        external_summary = (run.stage_summaries or {}).get("external_search") or {}
        external_executed = external_summary.get("status") in {
            "succeeded",
            "succeeded_partial",
            "succeeded_empty",
        }
        external_search_complete = (
            external_summary.get("status") in {"succeeded", "succeeded_empty"}
            and int(external_summary.get("failed_query_count") or 0) == 0
            and int(external_summary.get("exact_lookup_failure_count") or 0) == 0
        )
        external_verification_completed = (
            external_executed
            and external_search_complete
            and not self._external_selection_skipped(run)
        )
        supporting_checked = supporting.status == "succeeded"
        counter_checked = counter.status == "succeeded"
        blocking_missing: list[str] = []
        warnings: list[str] = []
        if len(seen_papers) < 2:
            blocking_missing.append("requires two independent full-text supporting papers")
        if not supporting_checked:
            blocking_missing.append(f"supporting evidence retrieval status is {supporting.status}")
        if not counter_checked:
            blocking_missing.append(f"counter evidence status is {counter.status}")
        if not external_verification_completed:
            warnings.append("external verification did not complete")
        coverage = self._evidence_coverage(candidate, valid)
        if coverage < 0.6:
            blocking_missing.append(
                "supporting evidence does not cover the opportunity's key problem and hypothesis"
            )
        confirmable = not blocking_missing
        verified = confirmable and not warnings
        missing = [*blocking_missing, *warnings]
        return {
            "verified": verified,
            "confirmable": confirmable,
            "independent_full_text_papers": len(seen_papers),
            "supporting_evidence_count": len(valid),
            "supporting_status": supporting.status,
            "counter_checked": counter_checked,
            "counter_status": counter.status,
            "external_search_executed": external_executed,
            "external_search_complete": external_search_complete,
            "external_verification_completed": external_verification_completed,
            "external_search_status": external_summary.get("status", "not_run"),
            "evidence_coverage": coverage,
            "reason": "verified"
            if verified
            else ("verified_with_warnings" if confirmable else "insufficient_full_text_evidence"),
            "blocking_missing": blocking_missing,
            "warnings": warnings,
            "missing": missing,
        }

    def _supporting_for_candidate(
        self, candidate: dict[str, Any], items: list[RetrievalResultItem]
    ) -> list[RetrievalResultItem]:
        fields = " ".join(
            str(candidate.get(key) or "")
            for key in (
                "problem_statement",
                "candidate_hypothesis",
                "why_existing_work_is_insufficient",
            )
        )
        return [item for item in items if self._text_relevant(fields, item.text)]

    @staticmethod
    def _text_relevant(candidate_text: str, evidence_text: str) -> bool:
        tokens = {token for token in re.findall(r"[a-zA-Z0-9]{4,}", candidate_text.lower())}
        evidence_tokens = {token for token in re.findall(r"[a-zA-Z0-9]{4,}", evidence_text.lower())}
        return len(tokens & evidence_tokens) >= 2

    def _evidence_coverage(
        self, candidate: dict[str, Any] | None, items: list[RetrievalResultItem]
    ) -> float:
        if not candidate or not items:
            return 0.0
        fields = [
            str(candidate.get("problem_statement") or ""),
            str(candidate.get("candidate_hypothesis") or ""),
            str(candidate.get("why_existing_work_is_insufficient") or ""),
        ]
# 针对候选的 semantic retrieval 已经匹配了联合 problem/hypothesis query。因此 coverage
# 衡量的是 proposal 是否结构完整、是否有独立且以 span 为锚点的论文支撑，而不是用 lexical
# regex 比较中文 proposal 文本和未翻译的英文 evidence。
        complete_fields = sum(bool(field.strip()) for field in fields)
        papers = len({item.paper_id for item in items if item.paper_id})
        evidence_density = min(len(items) / 3, 1.0)
        return round(
            min(
                1.0,
                (complete_fields / len(fields)) * 0.4
                + min(papers / 2, 1.0) * 0.4
                + evidence_density * 0.2,
            ),
            3,
        )

    def _has_valid_evidence_anchor(
        self, span: EvidenceSpan, evidence_text: str | None = None
    ) -> bool:
        if not span.artifact_id or span.start_char is None or span.end_char is None:
            return False
        if span.end_char <= span.start_char:
            return False
        artifact = self.db.get(Artifact, span.artifact_id)
        if artifact is None or artifact.is_deleted:
            return False
        if not evidence_text or not span.text:
            return True
        normalize = lambda value: " ".join(value.lower().split())
        span_text = normalize(span.text)
        item_text = normalize(evidence_text)
        if span_text in item_text or item_text in span_text:
            return True
        span_tokens = set(re.findall(r"[a-zA-Z0-9]{4,}", span_text))
        item_tokens = set(re.findall(r"[a-zA-Z0-9]{4,}", item_text))
        overlap = len(span_tokens & item_tokens)
        return overlap >= 3 and overlap / max(1, len(span_tokens)) >= 0.5

# ---------------------------------------------------------- synthesis（MA-1）：综合
# 委托给 SynthesisService（app.domains.discover.synthesis）。保留薄封装，以便现有调用方
#（orchestrator + tests）继续工作。
    def _synthesize_candidates(
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
        return self.synthesis.synthesize(
            run, claim_text, supporting, similar, counter, external_fulltext,
            gate, maximum, critic_feedback=critic_feedback,
        )

    @staticmethod
    
    @staticmethod
    def _normalize_candidate(value: dict[str, Any], gate: dict[str, Any], *, provider: str) -> dict[str, Any]:
        return normalize_candidate(value, gate, provider=provider)

    @staticmethod
    def _fallback_candidate(claim_text: str, supporting: RetrievalResponse, similar: RetrievalResponse, counter: RetrievalResponse, gate: dict[str, Any]) -> dict[str, Any]:
        return fallback_candidate(claim_text, supporting, similar, counter, gate)

    def _persist_candidates(
        self,
        run: DiscoverRun,
        claim: KnowledgeItem | None,
        claim_text: str,
        supporting: RetrievalResponse,
        similar: RetrievalResponse,
        counter: RetrievalResponse,
        external_fulltext: RetrievalResponse,
        candidates: list[dict[str, Any]],
    ) -> tuple[list[ResearchOpportunity], list[dict[str, Any]]]:
        existing = list(
            self.db.execute(
                select(ResearchOpportunity).where(
                    ResearchOpportunity.discover_run_id == run.id,
                    ResearchOpportunity.is_deleted.is_(False),
                )
            ).scalars()
        )
        if existing:
            gates = [
                (item.source_payload or {}).get(
                    "gate", {"verified": False, "reason": "idempotent_existing_result"}
                )
                for item in existing
            ]
            return existing, gates
        created: list[ResearchOpportunity] = []
        final_gates: list[dict[str, Any]] = []
        external_rows = list(
            self.db.execute(
                select(DiscoverExternalCandidate).where(
                    DiscoverExternalCandidate.discover_run_id == run.id
                )
            ).scalars()
        )
        for index, candidate in enumerate(candidates):
# 为每个具体 proposal 重新运行 supporting retrieval。synthesis 前的 topic retrieval 仅作为上下文。
            config = DiscoverConfig.model_validate(run.config or {})
            candidate_supporting = self._candidate_supporting(run, claim, candidate, config)
            gate = self._evidence_gate(
                run, candidate=candidate, supporting=candidate_supporting, counter=counter
            )
            candidate["evidence_coverage"] = gate["evidence_coverage"]
            candidate["verification_status"] = (
                "verified"
                if gate["verified"]
                else (
                    "verified_with_warnings" if gate["confirmable"] else "verification_incomplete"
                )
            )
            final_gates.append(gate)
            candidate_external_fulltext = self._external_fulltext(run, candidate_supporting)
            opportunity = ResearchOpportunity(
                id=str(uuid4()),
                workspace_id=run.workspace_id,
                claim_item_id=claim.id if claim else None,
                discover_run_id=run.id,
                title=candidate["title"],
                summary=candidate["problem_statement"],
                rationale=candidate["why_existing_work_is_insufficient"],
                suggested_directions=list(
                    (candidate.get("candidate_validation_plan") or {}).get("steps", [])
                )[:8],
                confidence=candidate["confidence"],
                status="candidate" if gate["confirmable"] else "needs_more_evidence",
                source_payload={"claim_text": claim_text, "gate": gate, "candidate_index": index, "synthesis_provider": candidate["provider"], "critic_review": candidate.get("critic_review"), "narrowing_pass": candidate.get("narrowing_pass"), "supporting_evidence": candidate_supporting.model_dump(mode="json"), "external_full_text": candidate_external_fulltext.model_dump(mode="json"), "similar_work": similar.model_dump(mode="json"), "counter_evidence": counter.model_dump(mode="json")},
                is_deleted=False,
            )
            self.db.add(opportunity)
            self.db.flush()
            version = OpportunityVersion(
                id=str(uuid4()),
                opportunity_id=opportunity.id,
                version_number=1,
                title=candidate["title"],
                problem_statement=candidate["problem_statement"],
                research_scope=candidate["research_scope"],
                why_existing_work_is_insufficient=candidate["why_existing_work_is_insufficient"],
                candidate_research_question=candidate["candidate_research_question"],
                candidate_hypothesis=candidate["candidate_hypothesis"],
                candidate_validation_plan=candidate["candidate_validation_plan"],
                open_risks=candidate["open_risks"],
                novelty_score=candidate["novelty_score"],
                feasibility_score=candidate["feasibility_score"],
                significance_score=candidate["significance_score"],
                confidence=candidate["confidence"],
                evidence_coverage=candidate["evidence_coverage"],
                verification_status=candidate["verification_status"],
                synthesis_metadata={
                    "provider": candidate["provider"],
                    "prompt_version": run.prompt_version,
                    "retrieval_snapshot_version": run.retrieval_snapshot_version,
                },
                created_by="agent",
            )
            self.db.add(version)
            self.db.flush()
            opportunity.current_version_id = version.id
            opportunity.status = "candidate" if gate["confirmable"] else "needs_more_evidence"
            self._persist_evidence(
                version.id, candidate_supporting, similar, counter, external_rows
            )
            created.append(opportunity)
            self.timeline.record(
                workspace_id=run.workspace_id,
                event_type="opportunity.generated",
                subject_type="opportunity",
                subject_id=opportunity.id,
                actor="agent",
                payload={
                    "run_id": run.id,
                    "version_id": version.id,
                    "verification_status": version.verification_status,
                },
            )
        self.db.commit()
        return created, final_gates

    def reassess_opportunity_gate(
        self,
        workspace_id: str,
        opportunity_id: str,
        *,
        actor: str = "user",
    ) -> ResearchOpportunity:
        """根据不可变证据快照重新计算已保存 opportunity 的 gate。

        该过程有意保持确定性，不进行新的 external 或 model 调用。Gate 逻辑升级时可以
        使用它重新评估，同时保留原始检索快照的可审计性。
        """
        opportunity = self.get_opportunity(workspace_id, opportunity_id)
        version = self._current_version(opportunity, None)
        if opportunity.discover_run_id is None:
            raise DiscoverInputError("该研究机会没有可复核的 Discover 运行快照")
        run = self.get_run(workspace_id, opportunity.discover_run_id)
        payload = dict(opportunity.source_payload or {})
        supporting_payload = payload.get("supporting_evidence")
        counter_payload = payload.get("counter_evidence")
        if not isinstance(supporting_payload, dict) or not isinstance(counter_payload, dict):
            raise DiscoverInputError("该研究机会缺少支持证据或反证检索快照")

        candidate = {
            "problem_statement": version.problem_statement,
            "candidate_hypothesis": version.candidate_hypothesis,
            "why_existing_work_is_insufficient": version.why_existing_work_is_insufficient,
        }
        gate = self._evidence_gate(
            run,
            candidate=candidate,
            supporting=RetrievalResponse.model_validate(supporting_payload),
            counter=RetrievalResponse.model_validate(counter_payload),
        )
        payload["gate"] = gate
        opportunity.source_payload = payload
        version.evidence_coverage = float(gate["evidence_coverage"])
        version.verification_status = (
            "verified"
            if gate["verified"]
            else ("verified_with_warnings" if gate["confirmable"] else "verification_incomplete")
        )
        if opportunity.status not in {"confirmed", "edited_confirmed", "rejected", "deferred"}:
            opportunity.status = "candidate" if gate["confirmable"] else "needs_more_evidence"
        self.db.commit()
        self.db.refresh(opportunity)
        self.timeline.record(
            workspace_id=workspace_id,
            event_type="opportunity.gate_reassessed",
            subject_type="opportunity",
            subject_id=opportunity.id,
            actor=actor,
            payload={
                "version_id": version.id,
                "confirmable": gate["confirmable"],
                "evidence_coverage": gate["evidence_coverage"],
                "independent_full_text_papers": gate["independent_full_text_papers"],
            },
        )
        return opportunity

    def _persist_evidence(
        self,
        version_id: str,
        supporting: RetrievalResponse,
        similar: RetrievalResponse,
        counter: RetrievalResponse,
        external_rows: list[DiscoverExternalCandidate],
    ) -> None:
        for rank, item in enumerate(supporting.items, start=1):
            span = self._find_evidence_span(item, supporting.workspace_id)
            self.db.add(OpportunityEvidence(id=str(uuid4()), opportunity_version_id=version_id, relation="supports", source_scope=item.source_scope, evidence_level=item.evidence_level, paper_id=item.paper_id, evidence_span_id=span.id if span else None, artifact_id=span.artifact_id if span else item.artifact_id, chunk_id=item.chunk_id, rank=rank, score=item.score, judgement="supports", judgement_confidence=item.judgement_confidence, display_excerpt=(item.text or "").replace("\x00", "")[:2000], snapshot_payload=item.model_dump(mode="json")))
        for rank, item in enumerate(similar.items, start=1):
            span = self._find_evidence_span(item, similar.workspace_id)
            self.db.add(OpportunityEvidence(id=str(uuid4()), opportunity_version_id=version_id, relation="similar", source_scope="workspace", evidence_level=item.evidence_level, paper_id=item.paper_id, evidence_span_id=span.id if span else None, artifact_id=span.artifact_id if span else item.artifact_id, chunk_id=item.chunk_id, rank=rank, score=item.score, display_excerpt=(item.text or "").replace("\x00", "")[:2000], snapshot_payload=item.model_dump(mode="json")))
        for rank, item in enumerate(counter.items, start=1):
            relation = (
                item.judgement
                if item.judgement in {"contradicts", "qualifies", "supports", "overlaps"}
                else "unknown"
            )
            span = self._find_evidence_span(item, counter.workspace_id)
            self.db.add(OpportunityEvidence(id=str(uuid4()), opportunity_version_id=version_id, relation=relation, source_scope="workspace", evidence_level=item.evidence_level, paper_id=item.paper_id, evidence_span_id=span.id if span else None, artifact_id=span.artifact_id if span else item.artifact_id, chunk_id=item.chunk_id, rank=rank, score=item.score, judgement=item.judgement, judgement_confidence=item.judgement_confidence, display_excerpt=(item.text or "").replace("\x00", "")[:2000], snapshot_payload=item.model_dump(mode="json")))
        for row in external_rows[:12]:
            self.db.add(
                OpportunityEvidence(
                    id=str(uuid4()),
                    opportunity_version_id=version_id,
                    relation=row.role,
                    source_scope="external",
                    evidence_level=row.evidence_level,
                    external_candidate_id=row.id,
                    paper_id=row.imported_paper_id,
                    rank=row.rank,
                    score=0.0,
                    display_excerpt=(row.abstract or row.title)[:2000],
                    snapshot_payload=row.snapshot_payload,
                )
            )

    def _find_evidence_span(
        self, item: RetrievalResultItem, workspace_id: str
    ) -> EvidenceSpan | None:
        if not item.paper_id or not item.text:
            return None
# 检索到的 chunk 可能仍携带旧解析产生的 NUL 字节（sanitize 之前的数据）。PostgreSQL 拒绝
# LIKE 参数中的 NUL，因此在查询前进行防御性移除。
        fragment = item.text[:80].replace("\x00", "")
        exact = self.db.execute(
            select(EvidenceSpan)
            .where(
                EvidenceSpan.workspace_id == workspace_id,
                EvidenceSpan.paper_id == item.paper_id,
                EvidenceSpan.relation == "supports",
                EvidenceSpan.is_deleted.is_(False),
                EvidenceSpan.text.contains(fragment),
            )
        ).scalars().first()
        if exact is not None:
            return exact
        spans = list(
            self.db.execute(
                select(EvidenceSpan)
                .where(
                    EvidenceSpan.workspace_id == workspace_id,
                    EvidenceSpan.paper_id == item.paper_id,
                    EvidenceSpan.relation == "supports",
                    EvidenceSpan.is_deleted.is_(False),
                )
                .order_by(EvidenceSpan.confidence.desc())
            ).scalars()
        )
        item_tokens = {token for token in re.findall(r"[a-zA-Z0-9]{4,}", item.text.lower())}
        return next(
            (
                span
                for span in spans
                if span.text
                and len(
                    item_tokens
                    & {token for token in re.findall(r"[a-zA-Z0-9]{4,}", span.text.lower())}
                )
                >= 2
            ),
            None,
        )

    def _find_span(self, item: RetrievalResultItem, workspace_id: str) -> str | None:
        span = self._find_evidence_span(item, workspace_id)
        return span.id if span else None

# -------------------------------------------------------------- 辅助函数
    def _resolve_claim(self, workspace_id: str, claim_item_id: str | None) -> KnowledgeItem | None:
        if not claim_item_id:
            return None
        claim = self.db.get(KnowledgeItem, claim_item_id)
        if (
            claim is None
            or claim.is_deleted
            or claim.workspace_id != workspace_id
            or claim.type != "claim"
        ):
            raise DiscoverInputError("claim_item_id must reference a claim in this workspace")
        return claim

    def _validate_papers(self, workspace_id: str, paper_ids: list[str]) -> None:
        if not paper_ids:
            return
        count = int(
            self.db.execute(
                select(func.count())
                .select_from(Paper)
                .where(
                    Paper.workspace_id == workspace_id,
                    Paper.id.in_(paper_ids),
                    Paper.is_deleted.is_(False),
                )
            ).scalar()
            or 0
        )
        if count != len(set(paper_ids)):
            raise DiscoverInputError("all selected papers must belong to this workspace")

    @staticmethod
    def _claim_text(item: KnowledgeItem | None) -> str:
        if item is None:
            return ""
        statement = (item.content or {}).get("statement")
        return (
            statement.strip()
            if isinstance(statement, str) and statement.strip()
            else item.canonical_name.strip()
        )

    @staticmethod
    def _empty_response(workspace_id: str, query: str, purpose: str) -> RetrievalResponse:
        return RetrievalResponse(
            request_id=str(uuid4()),
            workspace_id=workspace_id,
            query=query,
            purpose=purpose,
            status="succeeded",
            items=[],
            total=0,
        )


def resume_discover_runs_for_paper(db: Session, paper_id: str, workspace_id: str) -> None:
    """导入论文完全就绪后，恢复等待中的 Discover run。"""
    service = DiscoverService(db)
    candidate_rows = list(
        db.execute(
            select(DiscoverExternalCandidate).where(
                DiscoverExternalCandidate.imported_paper_id == paper_id,
                DiscoverExternalCandidate.verification_status.in_(
                    ["selected", "imported_pending_parse", "verification_failed", "verified"]
                ),
            )
        ).scalars()
    )
    run_ids = {row.discover_run_id for row in candidate_rows}
    for run_id in run_ids:
        run = db.get(DiscoverRun, run_id)
        if run is None or run.workspace_id != workspace_id or run.status != "waiting_for_fulltext":
            continue
        state = service._external_candidate_state(run)
        if state["pending"]:
            continue
        if not state["verified"]:
            service._wait_for_fulltext(run, state)
            continue
        run.status = "queued"
        run.stage = "fulltext_verification"
        run.progress = max(run.progress, 0.70)
        run.verification_status = "in_progress"
        run.stage_summaries = {
            **(run.stage_summaries or {}),
            "fulltext_verification": {
                "status": "succeeded",
                "verified": state["verified"],
                "failed": state["failed"],
                "resumed": True,
            },
        }
        try:
            if run.task_id:
                TaskService(db).resume_from_user(
                    run.task_id,
                    decision={"resumed_after_paper_id": paper_id},
                )
            db.commit()
            from app.workers.tasks.run_discover import spawn_discover_task

            celery_id = spawn_discover_task(run.id)
            if run.task_id:
                task = TaskService(db).get(run.task_id)
                task.celery_task_id = celery_id
            db.commit()
            service.timeline.record(
                workspace_id=run.workspace_id,
                event_type="discover.run_resumed",
                subject_type="discover_run",
                subject_id=run.id,
                actor="system",
                payload={"run_id": run.id, "paper_id": paper_id},
            )
        except Exception as exc:
            db.rollback()
            run = db.get(DiscoverRun, run_id)
            if run is not None:
                run.status = "waiting_for_fulltext"
                run.verification_status = "failed"
                run.stage_summaries = {
                    **(run.stage_summaries or {}),
                    "fulltext_verification": {
                        "status": "failed",
                        "retryable": True,
                        "error": str(exc)[:500],
                    },
                }
                db.commit()
