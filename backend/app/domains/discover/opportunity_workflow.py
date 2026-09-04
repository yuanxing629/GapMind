"""Opportunity workflow: listing, decisions (confirm/reject/defer/edit), and plan conversion.

This is one of the three sub-aggregates extracted from the original
``service.py``. The other two (``external_sourcing`` and the inline
orchestrator) still live next to it because their internal call structure
is too dense to split without growing the diff beyond useful.

Each method here used to live as a method on ``DiscoverService``; the
``_WorkflowHelper`` mixin pattern keeps the call sites in ``service.py``
working without rewiring every ``self.X`` access.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, or_, select

from app.core.config import settings
from app.domains.discover.exceptions import (
    DiscoverGateError,
    InvalidOpportunityTransition,
    OpportunityNotFoundError,
    OpportunityVersionConflict,
)
from app.domains.discover.models import (
    DiscoverRun,
    HumanDecision,
    OpportunityEvidence,
    OpportunityVersion,
    ResearchOpportunity,
    ResearchPlan,
)
from app.domains.discover.schemas import EvidenceManifest, EvidenceManifestItem

CLOSED_OPPORTUNITY_STATUSES = frozenset({"confirmed", "edited_confirmed", "rejected"})

from app.domains.artifact.models import Artifact
from app.domains.artifact.service import ArtifactService
from app.domains.knowledge.models import EvidenceSpan


class OpportunityWorkflow:
    """Mixin-style helpers for the Opportunity state machine.

    Mixed into ``DiscoverService``. Methods call ``self.db`` and
    ``self.timeline``; those are the only attributes they depend on from
    the outer service.
    """

    # ------------------------------------------------------- read paths
    @staticmethod
    def _evidence_freshness(
        evidence: list[OpportunityEvidence],
        *,
        now: datetime | None = None,
    ) -> tuple[str, datetime | None]:
        """Classify the age of the verification snapshot attached to evidence.

        This is an operational revalidation signal. It intentionally does not
        claim that a paper or scientific result has become invalid. Evidence
        rows without a recorded timestamp stay ``unknown`` rather than being
        silently treated as current.
        """
        if not evidence or any(row.created_at is None for row in evidence):
            return "unknown", None

        timestamps: list[datetime] = []
        for row in evidence:
            value = row.created_at
            if not isinstance(value, datetime):
                return "unknown", None
            timestamps.append(value if value.tzinfo else value.replace(tzinfo=timezone.utc))

        checked_at = max(timestamps)
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=timezone.utc)
        age = max(current_time - checked_at, timedelta(0))
        max_age = timedelta(days=max(settings.evidence_freshness_max_age_days, 1))
        if age <= max_age:
            return "current", checked_at
        if age <= max_age * 2:
            return "stale", checked_at
        return "expired", checked_at

    def list_opportunities(
        self,
        workspace_id: str,
        *,
        status_filter: str | None,
        run_id: str | None,
        pending_only: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[ResearchOpportunity], int]:
        base = (
            select(ResearchOpportunity)
            .outerjoin(
                DiscoverRun,
                ResearchOpportunity.discover_run_id == DiscoverRun.id,
            )
            .where(
                ResearchOpportunity.workspace_id == workspace_id,
                ResearchOpportunity.is_deleted.is_(False),
                or_(
                    ResearchOpportunity.discover_run_id.is_(None),
                    DiscoverRun.deleted_at.is_(None),
                ),
            )
        )
        if status_filter:
            base = base.where(ResearchOpportunity.status == status_filter)
        if pending_only:
            base = base.where(ResearchOpportunity.status.not_in(CLOSED_OPPORTUNITY_STATUSES))
        if run_id:
            base = base.where(ResearchOpportunity.discover_run_id == run_id)
        items = list(
            self.db.execute(
                base.order_by(ResearchOpportunity.created_at.desc()).limit(limit).offset(offset)
            ).scalars()
        )
        total = int(
            self.db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
        )
        return items, total

    def list_confirmed_portfolio(
        self,
        workspace_id: str,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return durable confirmed opportunities independent of run history visibility."""
        base = select(ResearchOpportunity).where(
            ResearchOpportunity.workspace_id == workspace_id,
            ResearchOpportunity.is_deleted.is_(False),
            ResearchOpportunity.status.in_({"confirmed", "edited_confirmed"}),
        )
        opportunities = list(
            self.db.execute(
                base.order_by(ResearchOpportunity.updated_at.desc()).limit(limit).offset(offset)
            ).scalars()
        )
        total = int(
            self.db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
        )
        if not opportunities:
            return [], total

        version_ids = [item.current_version_id for item in opportunities if item.current_version_id]
        versions = (
            {
                item.id: item
                for item in self.db.execute(
                    select(OpportunityVersion).where(OpportunityVersion.id.in_(version_ids))
                ).scalars()
            }
            if version_ids
            else {}
        )
        opportunity_ids = [item.id for item in opportunities]
        plans: dict[str, ResearchPlan] = {}
        for plan in self.db.execute(
            select(ResearchPlan)
            .where(ResearchPlan.opportunity_id.in_(opportunity_ids))
            .order_by(ResearchPlan.created_at.desc())
        ).scalars():
            plans.setdefault(plan.opportunity_id, plan)
        return [
            {
                "opportunity": item,
                "current_version": versions.get(item.current_version_id),
                "plan": plans.get(item.id),
            }
            for item in opportunities
        ], total

    def list_research_plans(
        self,
        workspace_id: str,
        *,
        status_filter: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[ResearchPlan], int]:
        base = (
            select(ResearchPlan)
            .outerjoin(ResearchOpportunity, ResearchPlan.opportunity_id == ResearchOpportunity.id)
            .where(
                ResearchPlan.workspace_id == workspace_id,
                (
                    (ResearchPlan.opportunity_id.is_(None))
                    | (ResearchOpportunity.is_deleted.is_(False))
                ),
            )
        )
        if status_filter:
            base = base.where(ResearchPlan.status == status_filter)
        plans = list(
            self.db.execute(
                base.order_by(ResearchPlan.updated_at.desc()).limit(limit).offset(offset)
            ).scalars()
        )
        total = int(
            self.db.execute(select(func.count()).select_from(base.subquery())).scalar() or 0
        )
        return plans, total

    def get_opportunity(self, workspace_id: str, opportunity_id: str) -> ResearchOpportunity:
        item = self.db.get(ResearchOpportunity, opportunity_id)
        if item is None or item.is_deleted or item.workspace_id != workspace_id:
            raise OpportunityNotFoundError(opportunity_id)
        return item

    def opportunity_detail(self, workspace_id: str, opportunity_id: str) -> dict[str, Any]:
        item = self.get_opportunity(workspace_id, opportunity_id)
        versions = list(
            self.db.execute(
                select(OpportunityVersion)
                .where(OpportunityVersion.opportunity_id == item.id)
                .order_by(OpportunityVersion.version_number.desc())
            ).scalars()
        )
        current = next(
            (v for v in versions if v.id == item.current_version_id),
            versions[0] if versions else None,
        )
        evidence = (
            list(
                self.db.execute(
                    select(OpportunityEvidence)
                    .where(OpportunityEvidence.opportunity_version_id == current.id)
                    .order_by(OpportunityEvidence.rank)
                ).scalars()
            )
            if current
            else []
        )
        decisions = list(
            self.db.execute(
                select(HumanDecision)
                .where(HumanDecision.opportunity_id == item.id)
                .order_by(HumanDecision.created_at.desc())
            ).scalars()
        )
        plan = (
            self.db.execute(
                select(ResearchPlan)
                .where(ResearchPlan.opportunity_id == item.id)
                .order_by(ResearchPlan.created_at.desc())
            )
            .scalars()
            .first()
        )
        return {
            "opportunity": item,
            "current_version": current,
            "versions": versions,
            "evidence": evidence,
            "evidence_manifest": self._build_evidence_manifest(item, current, evidence),
            "decisions": decisions,
            "plan": plan,
        }

    def _build_evidence_manifest(
        self,
        item: ResearchOpportunity,
        current: OpportunityVersion | None,
        evidence: list[OpportunityEvidence],
    ) -> EvidenceManifest | None:
        """Assemble the evidence-credibility passport for an opportunity.

        Aggregates counts, independent papers, full-text vs metadata, gate
        status, versions, critic verdict and human-review state from existing
        rows — a plain snapshot, no new tables. Returns ``None`` only when the
        opportunity has no version yet.
        """
        if current is None:
            return None
        source = item.source_payload or {}
        gate = source.get("gate") or {}
        critic = source.get("critic_review") or {}
        narrowing = source.get("narrowing_pass") or {}
        synthesis_meta = current.synthesis_metadata or {}
        evidence_freshness, evidence_checked_at = self._evidence_freshness(evidence)

        counts = {"supports": 0, "similar": 0, "counter": 0}
        papers: set[str] = set()
        full_text: set[str] = set()
        metadata_only: set[str] = set()
        external = 0
        items: list[EvidenceManifestItem] = []
        for ev in evidence:
            relation = ev.relation
            if relation == "supports":
                counts["supports"] += 1
            elif relation == "similar":
                counts["similar"] += 1
            else:
                counts["counter"] += 1
            if ev.source_scope == "external":
                external += 1
            if ev.paper_id:
                papers.add(ev.paper_id)
                if ev.evidence_level == "full_text":
                    full_text.add(ev.paper_id)
                else:
                    metadata_only.add(ev.paper_id)
            items.append(
                EvidenceManifestItem(
                    relation=relation,
                    source_scope=ev.source_scope,
                    evidence_level=ev.evidence_level,
                    paper_id=ev.paper_id,
                    external_candidate_id=ev.external_candidate_id,
                    rank=ev.rank,
                    judgement=ev.judgement,
                    judgement_confidence=ev.judgement_confidence,
                    display_excerpt=(ev.display_excerpt or "")[:200],
                )
            )
        return EvidenceManifest(
            source_type="opportunity",
            source_id=item.id,
            total=len(evidence),
            **counts,
            independent_papers=len(papers),
            full_text_papers=len(full_text),
            metadata_only_papers=len(metadata_only),
            external_sources=external,
            gate_verified=gate.get("verified"),
            gate_confirmable=gate.get("confirmable"),
            evidence_coverage=gate.get("evidence_coverage"),
            verification_status=getattr(current, "verification_status", None),
            critic_verdict=critic.get("verdict"),
            narrowing_outcome=narrowing.get("outcome"),
            prompt_version=synthesis_meta.get("prompt_version") or source.get("prompt_version"),
            model_name=synthesis_meta.get("provider"),
            corpus_version=synthesis_meta.get("corpus_version") or source.get("corpus_version"),
            human_status=item.status,
            evidence_freshness=evidence_freshness,
            evidence_checked_at=evidence_checked_at,
            items=items,
        )

    def versions(self, workspace_id: str, opportunity_id: str) -> list[OpportunityVersion]:
        item = self.get_opportunity(workspace_id, opportunity_id)
        return list(
            self.db.execute(
                select(OpportunityVersion)
                .where(OpportunityVersion.opportunity_id == item.id)
                .order_by(OpportunityVersion.version_number.desc())
            ).scalars()
        )

    # ----------------------------------------------------- evidence view
    def opportunity_evidence_context(self, workspace_id: str, evidence_id: str) -> dict[str, Any]:
        evidence = self.db.get(OpportunityEvidence, evidence_id)
        if evidence is None:
            raise OpportunityNotFoundError(evidence_id)
        version = self.db.get(OpportunityVersion, evidence.opportunity_version_id)
        opportunity = self.db.get(ResearchOpportunity, version.opportunity_id) if version else None
        if (
            opportunity is None
            or opportunity.workspace_id != workspace_id
            or opportunity.is_deleted
        ):
            raise OpportunityNotFoundError(evidence_id)

        result: dict[str, Any] = {
            "evidence": evidence,
            "available": False,
            "paper_id": evidence.paper_id,
            "artifact_id": evidence.artifact_id,
            "artifact_kind": None,
            "filename": None,
            "content": None,
            "start_char": None,
            "end_char": None,
            "message": "This metadata-only evidence has no local full-text anchor.",
        }
        if not evidence.evidence_span_id:
            return result
        span = self.db.scalar(
            select(EvidenceSpan).where(
                EvidenceSpan.id == evidence.evidence_span_id,
                EvidenceSpan.workspace_id == workspace_id,
                EvidenceSpan.is_deleted.is_(False),
            )
        )
        if span is None or not span.artifact_id:
            return result
        artifact = self.db.scalar(
            select(Artifact).where(
                Artifact.id == span.artifact_id,
                Artifact.workspace_id == workspace_id,
                Artifact.is_deleted.is_(False),
            )
        )
        if artifact is None:
            result["message"] = "The source artifact is no longer available."
            return result
        path = ArtifactService(self.db).resolve_abs_path(artifact)
        if not path.exists():
            result["message"] = "The source artifact file is missing on disk."
            return result
        result.update(
            {
                "available": True,
                "artifact_id": artifact.id,
                "artifact_kind": artifact.kind,
                "filename": artifact.original_filename,
                "content": path.read_text(encoding="utf-8"),
                "start_char": span.start_char,
                "end_char": span.end_char,
                "message": None,
            }
        )
        return result

    # ----------------------------------------------------- decision paths
    def confirm(
        self,
        workspace_id: str,
        opportunity_id: str,
        version_id: str | None,
        note: str | None,
        actor: str = "user",
    ) -> ResearchOpportunity:
        item = self.get_opportunity(workspace_id, opportunity_id)
        version = self._current_version(item, version_id)
        self._require_confirmable(item, version)
        item.status = "confirmed"
        self._decision(item, version, version, "confirm", note, None, actor=actor)
        self.db.commit()
        self.timeline.record(
            workspace_id=workspace_id,
            event_type="opportunity.confirmed",
            subject_type="opportunity",
            subject_id=item.id,
            actor=actor,
            payload={"version_id": version.id, "note": note},
        )
        return item

    def edit_confirm(
        self,
        workspace_id: str,
        opportunity_id: str,
        base_version_id: str,
        changes: dict[str, Any],
        note: str | None,
        actor: str = "user",
    ) -> ResearchOpportunity:
        item = self.get_opportunity(workspace_id, opportunity_id)
        base = self._current_version(item, base_version_id)
        if item.current_version_id != base_version_id:
            raise OpportunityVersionConflict("Opportunity has changed; refresh before editing")
        self._require_confirmable(item, base)
        data = {
            key: getattr(base, key)
            for key in (
                "title",
                "problem_statement",
                "research_scope",
                "why_existing_work_is_insufficient",
                "candidate_research_question",
                "candidate_hypothesis",
                "candidate_validation_plan",
                "open_risks",
                "novelty_score",
                "feasibility_score",
                "significance_score",
                "confidence",
                "evidence_coverage",
                "verification_status",
                "synthesis_metadata",
            )
        }
        for key, value in changes.items():
            if key in data:
                data[key] = value
        number = (
            int(
                self.db.execute(
                    select(func.max(OpportunityVersion.version_number)).where(
                        OpportunityVersion.opportunity_id == item.id
                    )
                ).scalar()
                or 0
            )
            + 1
        )
        new_version = OpportunityVersion(
            id=str(uuid4()),
            opportunity_id=item.id,
            version_number=number,
            created_by="user",
            **data,
        )
        self.db.add(new_version)
        self.db.flush()
        item.current_version_id = new_version.id
        item.status = "edited_confirmed"

        old_evidence = list(
            self.db.execute(
                select(OpportunityEvidence).where(
                    OpportunityEvidence.opportunity_version_id == base.id
                )
            ).scalars()
        )
        for ev in old_evidence:
            self.db.add(
                OpportunityEvidence(
                    id=str(uuid4()),
                    opportunity_version_id=new_version.id,
                    relation=ev.relation,
                    source_scope=ev.source_scope,
                    evidence_level=ev.evidence_level,
                    paper_id=ev.paper_id,
                    external_candidate_id=ev.external_candidate_id,
                    evidence_span_id=ev.evidence_span_id,
                    artifact_id=ev.artifact_id,
                    chunk_id=ev.chunk_id,
                    rank=ev.rank,
                    score=ev.score,
                    judgement=ev.judgement,
                    judgement_confidence=ev.judgement_confidence,
                    display_excerpt=ev.display_excerpt,
                    snapshot_payload=ev.snapshot_payload,
                )
            )
        self._decision(item, base, new_version, "edit_confirm", note, None)
        self.db.commit()
        self.timeline.record(
            workspace_id=workspace_id,
            event_type="opportunity.edited_confirmed",
            subject_type="opportunity",
            subject_id=item.id,
            actor=actor,
            payload={"from_version_id": base.id, "to_version_id": new_version.id},
        )
        return item

    def reject(
        self,
        workspace_id: str,
        opportunity_id: str,
        note: str | None,
        actor: str = "user",
    ) -> ResearchOpportunity:
        return self._simple_decision(
            workspace_id, opportunity_id, "rejected", "reject", note, None, actor=actor
        )

    def defer(
        self,
        workspace_id: str,
        opportunity_id: str,
        note: str | None,
        condition: str | None,
        actor: str = "user",
    ) -> ResearchOpportunity:
        return self._simple_decision(
            workspace_id, opportunity_id, "deferred", "defer", note, condition, actor=actor
        )

    def convert_to_plan(
        self,
        workspace_id: str,
        opportunity_id: str,
        actor: str = "user",
    ) -> ResearchPlan:
        item = self.get_opportunity(workspace_id, opportunity_id)
        if item.status not in {"confirmed", "edited_confirmed"}:
            raise DiscoverGateError(
                "plan_requires_confirmed_opportunity",
                "Only a confirmed opportunity can become a research plan",
            )
        version = self._current_version(item, None)
        existing = (
            self.db.execute(
                select(ResearchPlan).where(
                    ResearchPlan.opportunity_id == item.id,
                    ResearchPlan.opportunity_version_id == version.id,
                )
            )
            .scalars()
            .first()
        )
        if existing:
            return existing
        plan_data = version.candidate_validation_plan or {}
        translations = {
            "Select datasets and baselines": "依据研究问题选择数据集与基线方法",
            "Compare against the strongest similar-work setting": "在统一数据划分与训练预算下比较最强相似工作",
            "Run an ablation for the suspected boundary condition": "针对候选机制与边界条件开展消融实验",
            "External full-text verification is incomplete.": "外部论文全文核验尚未完成，可能遗漏直接相似工作。",
        }

        def plan_values(key: str, defaults: list[str]) -> list[str]:
            raw = plan_data.get(key)
            if not isinstance(raw, list):
                return defaults
            values = [
                translations.get(str(value).strip(), str(value).strip())
                for value in raw
                if value is not None and str(value).strip()
            ]
            return values or defaults

        run = self.get_run(workspace_id, item.discover_run_id) if item.discover_run_id else None
        constraints = (run.input_payload or {}).get("constraints", "") if run else ""
        plan = ResearchPlan(
            id=str(uuid4()),
            workspace_id=workspace_id,
            opportunity_id=item.id,
            opportunity_version_id=version.id,
            source_type="opportunity",
            status="draft",
            title=version.title or item.title or "未命名研究计划",
            research_question=version.candidate_research_question,
            hypothesis=version.candidate_hypothesis,
            scope_and_assumptions=version.research_scope,
            datasets=plan_values(
                "datasets",
                ["至少两个覆盖不同数据特征的公开基准数据集（待深度研究后确定）"],
            ),
            baselines=plan_values(
                "baselines",
                ["当前最强相似工作", "移除候选核心机制的消融基线"],
            ),
            metrics=plan_values(
                "metrics",
                ["目标问题对应的核心效果指标", "有效性或任务性能", "计算与存储开销"],
            ),
            validation_steps=plan_values(
                "steps",
                [
                    "依据研究问题选择至少两个公开数据集",
                    "复现最强相似工作并统一数据划分与训练预算",
                    "实现候选方法并开展核心组件消融实验",
                    "使用多随机种子和统计检验比较主要指标",
                ],
            ),
            expected_supporting_result=str(
                plan_data.get("expected_supporting_result")
                or "候选方法在多个数据集和随机种子上稳定改善核心指标，且不会造成不可接受的性能或资源开销退化。"
            ),
            falsification_criteria=str(
                plan_data.get("falsification_criteria")
                or "若核心指标提升不显著、无法跨数据集复现，或以明显损害有效性与可解释性为代价，则拒绝或收缩该假设。"
            ),
            risks=[
                translations.get(str(value).strip(), str(value).strip())
                for value in version.open_risks
                if value is not None and str(value).strip()
            ]
            or ["直接相似工作可能尚未被当前语料和外部检索完整覆盖。"],
            resource_constraints=str(constraints),
        )
        self.db.add(plan)
        self.db.commit()
        self.db.refresh(plan)
        self.timeline.record(
            workspace_id=workspace_id,
            event_type="plan.generated",
            subject_type="research_plan",
            subject_id=plan.id,
            actor=actor,
            payload={"opportunity_id": item.id, "version_id": version.id},
        )
        return plan

    # ------------------------------------------------------- internal helpers
    def _simple_decision(
        self,
        workspace_id: str,
        opportunity_id: str,
        status: str,
        action: str,
        note: str | None,
        condition: str | None,
        actor: str = "user",
    ) -> ResearchOpportunity:
        item = self.get_opportunity(workspace_id, opportunity_id)
        version = self._current_version(item, None)
        if item.status in {"confirmed", "edited_confirmed"} and status in {"rejected", "deferred"}:
            raise InvalidOpportunityTransition(
                "A confirmed opportunity cannot be rejected or deferred"
            )
        item.status = status
        self._decision(item, version, version, action, note, condition, actor=actor)
        self.db.commit()
        event = {"reject": "opportunity.rejected", "defer": "opportunity.deferred"}[action]
        self.timeline.record(
            workspace_id=workspace_id,
            event_type=event,
            subject_type="opportunity",
            subject_id=item.id,
            actor=actor,
            payload={"version_id": version.id, "note": note, "defer_condition": condition},
        )
        return item

    def _decision(
        self,
        item: ResearchOpportunity,
        from_version: OpportunityVersion,
        to_version: OpportunityVersion,
        action: str,
        note: str | None,
        condition: str | None,
        actor: str = "user",
    ) -> None:
        self.db.add(
            HumanDecision(
                id=str(uuid4()),
                opportunity_id=item.id,
                from_version_id=from_version.id,
                to_version_id=to_version.id,
                action=action,
                reason=note,
                defer_condition=condition,
                actor=actor,
            )
        )

    def _current_version(
        self, item: ResearchOpportunity, version_id: str | None
    ) -> OpportunityVersion:
        target_id = version_id or item.current_version_id
        version = self.db.get(OpportunityVersion, target_id) if target_id else None
        if version is None or version.opportunity_id != item.id:
            raise OpportunityVersionConflict("Requested version is not part of this opportunity")
        return version

    def _require_confirmable(self, item: ResearchOpportunity, version: OpportunityVersion) -> None:
        evidence_rows = list(
            self.db.execute(
                select(OpportunityEvidence).where(
                    OpportunityEvidence.opportunity_version_id == version.id,
                    OpportunityEvidence.relation == "supports",
                    OpportunityEvidence.judgement == "supports",
                    OpportunityEvidence.evidence_level == "full_text",
                )
            ).scalars()
        )
        independent_papers = {
            ev.paper_id
            for ev in evidence_rows
            if ev.paper_id and ev.evidence_span_id and ev.artifact_id
        }
        gate = (item.source_payload or {}).get("gate")
        blocking_missing: list[str] = []
        if isinstance(gate, dict):
            raw_blocking = gate.get("blocking_missing")
            if isinstance(raw_blocking, list):
                blocking_missing = [value for value in raw_blocking if isinstance(value, str)]
            else:
                raw_missing = gate.get("missing")
                if isinstance(raw_missing, list):
                    blocking_missing = [
                        value
                        for value in raw_missing
                        if isinstance(value, str)
                        and value != "external verification did not complete"
                    ]
        elif version.verification_status not in {"verified", "verified_with_warnings"}:
            blocking_missing = [f"verification status is {version.verification_status}"]
        if version.evidence_coverage < 0.6 or len(independent_papers) < 2 or blocking_missing:
            raise DiscoverGateError(
                "insufficient_full_text_evidence",
                "At least two independent full-text evidence papers are required before confirmation",
            )


__all__ = ["OpportunityWorkflow"]
