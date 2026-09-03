"""Persistence, conservative normalization, and deterministic board projection."""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domains.gap.context import get_gap_context_identity
from app.domains.gap.models import (
    GapBoardSnapshot,
    GapCanonicalConcept,
    GapConceptAssignment,
    PaperGapAnnotation,
)
from app.domains.gap.normalization import canonical_axis_label
from app.domains.gap.schemas import GapAnnotationOutput
from app.domains.knowledge.models import ExtractionRun
from app.domains.paper.models import Paper

NORMALIZE_PUNCTUATION = re.compile(r"[\s\-_—–·,，。；;：:（）()\[\]【】/]+")


class GapAnnotationNotFoundError(Exception):
    pass


class GapBoardNotFoundError(Exception):
    pass


class GapCellNotFoundError(Exception):
    pass


def normalization_key(value: str) -> str:
    return NORMALIZE_PUNCTUATION.sub("", value.strip().lower())


class GapService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_annotations(
        self, workspace_id: str, *, status: str | None = None
    ) -> list[PaperGapAnnotation]:
        query = select(PaperGapAnnotation).where(
            PaperGapAnnotation.workspace_id == workspace_id,
            PaperGapAnnotation.is_deleted.is_(False),
        )
        if status:
            query = query.where(PaperGapAnnotation.status == status)
        return list(self.db.execute(query.order_by(PaperGapAnnotation.updated_at.desc())).scalars())

    def get_annotation(self, workspace_id: str, annotation_id: str) -> PaperGapAnnotation:
        row = self.db.get(PaperGapAnnotation, annotation_id)
        if row is None or row.workspace_id != workspace_id or row.is_deleted:
            raise GapAnnotationNotFoundError(annotation_id)
        return row

    def annotation_is_stale(self, annotation: PaperGapAnnotation) -> bool:
        """Whether a newer knowledge context exists for this annotation's paper."""
        paper = self.db.get(Paper, annotation.paper_id)
        if paper is None or paper.is_deleted:
            return False
        identity = get_gap_context_identity(self.db, paper)
        return (
            identity.knowledge_extraction_run_id is not None
            and annotation.knowledge_extraction_run_id
            != identity.knowledge_extraction_run_id
        )

    def assign_annotation(self, annotation: PaperGapAnnotation) -> None:
        if annotation.status != "valid" or not annotation.output:
            return
        output = GapAnnotationOutput.model_validate(annotation.output)
        existing = list(
            self.db.execute(
                select(GapConceptAssignment).where(
                    GapConceptAssignment.annotation_id == annotation.id
                )
            ).scalars()
        )
        for item in existing:
            self.db.delete(item)
        self.db.flush()

        for method in output.methods:
            concept, mapping_method, confidence = self._concept(
                annotation.workspace_id,
                "method",
                method.method_strategy_zh,
                method.mechanism_zh,
            )
            self.db.add(
                GapConceptAssignment(
                    id=str(uuid4()),
                    annotation_id=annotation.id,
                    concept_id=concept.id,
                    axis_type="method",
                    local_entity_id=method.corresponding_entity_id,
                    original_label=method.method_strategy_zh,
                    mapping_method=mapping_method,
                    confidence=confidence,
                )
            )
        for problem in output.problems:
            concept, mapping_method, confidence = self._concept(
                annotation.workspace_id,
                "problem",
                problem.problem_label_zh,
                problem.description_zh,
            )
            self.db.add(
                GapConceptAssignment(
                    id=str(uuid4()),
                    annotation_id=annotation.id,
                    concept_id=concept.id,
                    axis_type="problem",
                    local_entity_id=problem.corresponding_entity_id,
                    original_label=problem.problem_label_zh,
                    mapping_method=mapping_method,
                    confidence=confidence,
                )
            )
        self.db.commit()

    def _concept(
        self,
        workspace_id: str,
        axis_type: str,
        label: str,
        description: str,
    ) -> tuple[GapCanonicalConcept, str, float]:
        taxonomy = canonical_axis_label(axis_type, label, description)
        canonical_label = taxonomy.label if taxonomy else label
        key = normalization_key(canonical_label)
        exact = self.db.execute(
            select(GapCanonicalConcept).where(
                GapCanonicalConcept.workspace_id == workspace_id,
                GapCanonicalConcept.axis_type == axis_type,
                GapCanonicalConcept.normalization_key == key,
                GapCanonicalConcept.is_deleted.is_(False),
            )
        ).scalar_one_or_none()
        if exact is not None:
            if label not in exact.aliases and label != exact.canonical_label:
                exact.aliases = [*exact.aliases, label]
            if not exact.description and description:
                exact.description = description
            if taxonomy:
                exact.status = "auto_taxonomy"
                return exact, taxonomy.rule_id, taxonomy.confidence
            return exact, "exact", 1.0

        concepts = list(
            self.db.execute(
                select(GapCanonicalConcept).where(
                    GapCanonicalConcept.workspace_id == workspace_id,
                    GapCanonicalConcept.axis_type == axis_type,
                    GapCanonicalConcept.is_deleted.is_(False),
                )
            ).scalars()
        )
        best: GapCanonicalConcept | None = None
        best_ratio = 0.0
        if taxonomy is None:
            for concept in concepts:
                ratio = SequenceMatcher(None, key, concept.normalization_key).ratio()
                if ratio > best_ratio:
                    best, best_ratio = concept, ratio
        # Conservative: uncertain pairs remain separate and can later be merged
        # by an online adjudicator or a human reviewer.
        if best is not None and best_ratio >= 0.92:
            if label not in best.aliases and label != best.canonical_label:
                best.aliases = [*best.aliases, label]
            if not best.description and description:
                best.description = description
            best.status = "auto_fuzzy"
            return best, "fuzzy", best_ratio

        concept = GapCanonicalConcept(
            id=str(uuid4()),
            workspace_id=workspace_id,
            axis_type=axis_type,
            canonical_label=canonical_label,
            normalization_key=key,
            aliases=[label] if label != canonical_label else [],
            description=description,
            status="auto_taxonomy" if taxonomy else "auto_exact",
            is_deleted=False,
        )
        self.db.add(concept)
        self.db.flush()
        if taxonomy:
            return concept, taxonomy.rule_id, taxonomy.confidence
        return concept, "new", 1.0

    def rebuild_board(
        self, workspace_id: str, *, paper_ids: list[str] | None = None
    ) -> GapBoardSnapshot:
        annotations = self._latest_valid_annotations(workspace_id, paper_ids=paper_ids or [])
        # Re-project assignments on every rebuild so taxonomy revisions are
        # applied to existing valid annotations without regenerating model
        # output.  Old unreferenced concepts remain auditable but are excluded
        # from the snapshot below.
        for annotation in annotations:
            self.assign_annotation(annotation)

        annotation_ids = [item.id for item in annotations]
        assignments = (
            list(
                self.db.execute(
                    select(GapConceptAssignment).where(
                        GapConceptAssignment.annotation_id.in_(annotation_ids)
                    )
                ).scalars()
            )
            if annotation_ids
            else []
        )
        concepts_by_id = {
            item.id: item
            for item in self.db.execute(
                select(GapCanonicalConcept).where(
                    GapCanonicalConcept.workspace_id == workspace_id,
                    GapCanonicalConcept.is_deleted.is_(False),
                )
            ).scalars()
        }
        assignment_by_local = {
            (item.annotation_id, item.local_entity_id): item for item in assignments
        }

        papers_by_concept: dict[str, set[str]] = defaultdict(set)
        relation_papers: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        for annotation in annotations:
            output = GapAnnotationOutput.model_validate(annotation.output)
            for assignment in assignments:
                if assignment.annotation_id == annotation.id:
                    papers_by_concept[assignment.concept_id].add(annotation.paper_id)
            for relation in output.relations:
                if relation.relation_type not in {"ADDRESSES", "HAS_LIMITATION"}:
                    continue
                source = assignment_by_local.get((annotation.id, relation.source_entity_id))
                target = assignment_by_local.get((annotation.id, relation.target_entity_id))
                if source is None or target is None:
                    continue
                relation_papers[(source.concept_id, target.concept_id, relation.relation_type)].add(
                    annotation.paper_id
                )

        method_concepts = sorted(
            (
                concept
                for concept in concepts_by_id.values()
                if concept.axis_type == "method" and concept.id in papers_by_concept
            ),
            key=lambda item: (-len(papers_by_concept[item.id]), item.canonical_label),
        )
        problem_concepts = sorted(
            (
                concept
                for concept in concepts_by_id.values()
                if concept.axis_type == "problem" and concept.id in papers_by_concept
            ),
            key=lambda item: (-len(papers_by_concept[item.id]), item.canonical_label),
        )
        max_method_count = max(
            (len(papers_by_concept[item.id]) for item in method_concepts), default=1
        )
        max_problem_count = max(
            (len(papers_by_concept[item.id]) for item in problem_concepts), default=1
        )

        addressed_problems_by_method: dict[str, set[str]] = defaultdict(set)
        addressing_methods_by_problem: dict[str, set[str]] = defaultdict(set)
        for (method_id, problem_id, relation_type), paper_set in relation_papers.items():
            if relation_type == "ADDRESSES" and paper_set:
                addressed_problems_by_method[method_id].add(problem_id)
                addressing_methods_by_problem[problem_id].add(method_id)
        max_method_degree = (
            max(
                (len(addressed_problems_by_method[item.id]) for item in method_concepts),
                default=1,
            )
            or 1
        )
        max_problem_degree = (
            max(
                (len(addressing_methods_by_problem[item.id]) for item in problem_concepts),
                default=1,
            )
            or 1
        )

        cells: list[dict[str, Any]] = []
        for method in method_concepts:
            for problem in problem_concepts:
                addressed = sorted(relation_papers[(method.id, problem.id, "ADDRESSES")])
                limitations = sorted(relation_papers[(method.id, problem.id, "HAS_LIMITATION")])
                explicit = bool(limitations)
                method_count = len(papers_by_concept[method.id])
                problem_count = len(papers_by_concept[problem.id])
                cooccurrence = sorted(papers_by_concept[method.id] & papers_by_concept[problem.id])
                cross_paper_support = method_count >= 2 and problem_count >= 2
                eligible = bool(explicit or cooccurrence or cross_paper_support)
                score = 0.0
                tier = "covered"
                reasons: list[str] = []
                if not addressed:
                    method_frequency = method_count / max_method_count
                    problem_frequency = problem_count / max_problem_count
                    method_degree = len(addressed_problems_by_method[method.id]) / max_method_degree
                    problem_degree = (
                        len(addressing_methods_by_problem[problem.id]) / max_problem_degree
                    )
                    score = min(
                        1.0,
                        (0.45 if explicit else 0.0)
                        + (0.15 if cooccurrence else 0.0)
                        + (0.05 if cross_paper_support else 0.0)
                        + 0.10 * method_frequency
                        + 0.15 * problem_frequency
                        + 0.05 * method_degree
                        + 0.10 * problem_degree,
                    )
                    if explicit:
                        tier = "explicit_limitation"
                        reasons.append(
                            f"{len(limitations)} 篇论文明确报告该方法族在此问题上的剩余局限"
                        )
                    elif cooccurrence:
                        tier = "same_paper_unlinked"
                        reasons.append(
                            f"方法族与问题族在 {len(cooccurrence)} 篇论文中共现，但未形成 ADDRESSES"
                        )
                    elif cross_paper_support:
                        tier = "cross_paper_transfer"
                        reasons.append(
                            f"方法族获 {method_count} 篇论文支持，问题族获 {problem_count} 篇论文支持"
                        )
                    else:
                        tier = "corpus_only"
                        reasons.append("仅由方法×问题组合产生，当前缺少直接关联证据")
                cells.append(
                    {
                        "method_concept_id": method.id,
                        "problem_concept_id": problem.id,
                        "addressed": bool(addressed),
                        "addressed_paper_ids": addressed,
                        "limitation_paper_ids": limitations,
                        "cooccurrence_paper_ids": cooccurrence,
                        "explicit_limitation": explicit,
                        "candidate_score": round(score, 4),
                        "candidate_tier": tier,
                        "candidate_reasons": reasons,
                        "eligible_for_discovery": eligible,
                        "verification_status": (
                            "covered"
                            if addressed
                            else "recommended"
                            if eligible
                            else "low_evidence"
                        ),
                    }
                )

        method_axes = [
            {
                "concept_id": item.id,
                "label": item.canonical_label,
                "aliases": item.aliases,
                "paper_count": len(papers_by_concept[item.id]),
                "paper_ids": sorted(papers_by_concept[item.id]),
            }
            for item in method_concepts
        ]
        problem_axes = [
            {
                "concept_id": item.id,
                "label": item.canonical_label,
                "aliases": item.aliases,
                "paper_count": len(papers_by_concept[item.id]),
                "paper_ids": sorted(papers_by_concept[item.id]),
            }
            for item in problem_concepts
        ]
        previous_version = (
            self.db.execute(
                select(func.max(GapBoardSnapshot.version)).where(
                    GapBoardSnapshot.workspace_id == workspace_id
                )
            ).scalar_one_or_none()
            or 0
        )
        snapshot = GapBoardSnapshot(
            id=str(uuid4()),
            workspace_id=workspace_id,
            version=previous_version + 1,
            filters={
                "paper_ids": sorted(paper_ids or []),
                "normalization_version": "gnn-explanation-taxonomy-v1",
                "candidate_scoring_version": "evidence-priority-v2",
                "uncovered_count": sum(1 for item in cells if not item["addressed"]),
            },
            method_axes=method_axes,
            problem_axes=problem_axes,
            cells=cells,
            source_annotation_ids=annotation_ids,
            candidate_count=sum(
                1 for item in cells if not item["addressed"] and item["eligible_for_discovery"]
            ),
            is_deleted=False,
        )
        self.db.add(snapshot)
        self.db.commit()
        self.db.refresh(snapshot)
        return snapshot

    def latest_board(self, workspace_id: str) -> GapBoardSnapshot:
        row = self.db.execute(
            select(GapBoardSnapshot)
            .where(
                GapBoardSnapshot.workspace_id == workspace_id,
                GapBoardSnapshot.is_deleted.is_(False),
            )
            .order_by(GapBoardSnapshot.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            raise GapBoardNotFoundError(workspace_id)
        return row

    def candidate_context(
        self, workspace_id: str, method_concept_id: str, problem_concept_id: str
    ) -> dict[str, Any]:
        board = self.latest_board(workspace_id)
        method = next(
            (item for item in board.method_axes if item["concept_id"] == method_concept_id), None
        )
        problem = next(
            (item for item in board.problem_axes if item["concept_id"] == problem_concept_id), None
        )
        cell = next(
            (
                item
                for item in board.cells
                if item["method_concept_id"] == method_concept_id
                and item["problem_concept_id"] == problem_concept_id
            ),
            None,
        )
        if method is None or problem is None or cell is None:
            raise GapCellNotFoundError(f"{method_concept_id}:{problem_concept_id}")
        return {"board": board, "method": method, "problem": problem, "cell": cell}

    def _latest_valid_annotations(
        self, workspace_id: str, *, paper_ids: list[str]
    ) -> list[PaperGapAnnotation]:
        query = (
            select(PaperGapAnnotation)
            .where(
                PaperGapAnnotation.workspace_id == workspace_id,
                PaperGapAnnotation.status == "valid",
                PaperGapAnnotation.is_deleted.is_(False),
            )
            .order_by(PaperGapAnnotation.updated_at.desc())
        )
        if paper_ids:
            query = query.where(PaperGapAnnotation.paper_id.in_(paper_ids))
        rows = list(self.db.execute(query).scalars())
        scoped_paper_ids = {row.paper_id for row in rows}
        papers = {
            item.id: item
            for item in self.db.execute(
                select(Paper).where(
                    Paper.workspace_id == workspace_id,
                    Paper.id.in_(scoped_paper_ids),
                    Paper.is_deleted.is_(False),
                )
            ).scalars()
        } if scoped_paper_ids else {}
        current_run_ids: dict[str, str] = {}
        if scoped_paper_ids and settings.gap_extraction_context_mode != "core_markdown_legacy_v1":
            runs = self.db.execute(
                select(ExtractionRun)
                .where(
                    ExtractionRun.workspace_id == workspace_id,
                    ExtractionRun.paper_id.in_(scoped_paper_ids),
                    ExtractionRun.status == "succeeded",
                )
                .order_by(ExtractionRun.finished_at.desc(), ExtractionRun.updated_at.desc())
            ).scalars()
            for run in runs:
                paper = papers.get(run.paper_id)
                if paper is None or run.artifact_id != paper.parsed_markdown_artifact_id:
                    continue
                current_run_ids.setdefault(run.paper_id, run.id)
        latest: dict[str, PaperGapAnnotation] = {}
        for row in rows:
            current_run_id = current_run_ids.get(row.paper_id)
            if current_run_id is not None and row.knowledge_extraction_run_id != current_run_id:
                # A successful knowledge run supersedes legacy or older gap
                # output until the specialized extractor is run again.
                continue
            latest.setdefault(row.paper_id, row)
        return list(latest.values())
