"""有界 PostgreSQL GraphRAG 投影测试。"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.domains.knowledge.graphrag import (
    _evidence_relevance_score,
    build_bounded_projection,
)
from app.domains.knowledge.models import (
    CanonicalEntity,
    EvidenceSpan,
    KnowledgeItem,
    KnowledgeRelation,
    PaperMention,
)
from app.domains.paper.models import Paper
from app.domains.retrieval.schemas import RetrievalResultItem
from app.domains.workspace.models import Workspace


def _id() -> str:
    return str(uuid4())


def test_bounded_projection_keeps_provenance_and_workspace_boundaries(db_session):
    workspace_id = _id()
    other_workspace_id = _id()
    db_session.add_all(
        [
            Workspace(id=workspace_id, name="Graph workspace", owner_id="user"),
            Workspace(id=other_workspace_id, name="Other workspace", owner_id="user"),
        ]
    )
    paper_one_id = _id()
    paper_two_id = _id()
    other_paper_id = _id()
    db_session.add_all(
        [
            Paper(id=paper_one_id, workspace_id=workspace_id, title="Seed paper"),
            Paper(id=paper_two_id, workspace_id=workspace_id, title="Related paper"),
            Paper(id=other_paper_id, workspace_id=other_workspace_id, title="Isolated paper"),
        ]
    )
    entity_id = _id()
    other_entity_id = _id()
    db_session.add_all(
        [
            CanonicalEntity(
                id=entity_id,
                workspace_id=workspace_id,
                type="method",
                canonical_name="Shared Method",
                normalization_key="shared method",
                status="human_confirmed",
            ),
            CanonicalEntity(
                id=other_entity_id,
                workspace_id=other_workspace_id,
                type="method",
                canonical_name="Shared Method",
                normalization_key="shared method",
                status="human_confirmed",
            ),
        ]
    )
    item_one_id = _id()
    item_two_id = _id()
    claim_id = _id()
    item_other_id = _id()
    db_session.add_all(
        [
            KnowledgeItem(
                id=item_one_id,
                workspace_id=workspace_id,
                paper_id=paper_one_id,
                canonical_entity_id=entity_id,
                type="method",
                canonical_name="Shared Method",
                content={"description": "seed"},
                source_provenance={"paper_id": paper_one_id},
                status="human_confirmed",
            ),
            KnowledgeItem(
                id=item_two_id,
                workspace_id=workspace_id,
                paper_id=paper_two_id,
                canonical_entity_id=entity_id,
                type="method",
                canonical_name="Shared Method",
                content={"description": "related"},
                source_provenance={"paper_id": paper_two_id},
                status="extracted_candidate",
            ),
            # 格式错误的 canonical link 不得使 claim context 跨论文串联。
            KnowledgeItem(
                id=claim_id,
                workspace_id=workspace_id,
                paper_id=paper_one_id,
                canonical_entity_id=entity_id,
                type="claim",
                canonical_name="Same label, different claim",
                content={"statement": "paper-local claim"},
                source_provenance={"paper_id": paper_one_id},
                status="human_confirmed",
            ),
            KnowledgeItem(
                id=item_other_id,
                workspace_id=other_workspace_id,
                paper_id=other_paper_id,
                canonical_entity_id=other_entity_id,
                type="method",
                canonical_name="Shared Method",
                content={"description": "must stay isolated"},
                source_provenance={"paper_id": other_paper_id},
                status="human_confirmed",
            ),
        ]
    )
    mention_one_id = _id()
    mention_two_id = _id()
    db_session.add_all(
        [
            PaperMention(
                id=mention_one_id,
                workspace_id=workspace_id,
                paper_id=paper_one_id,
                canonical_entity_id=entity_id,
                knowledge_item_id=item_one_id,
                mention_text="Shared Method",
                status="human_confirmed",
            ),
            PaperMention(
                id=mention_two_id,
                workspace_id=workspace_id,
                paper_id=paper_two_id,
                canonical_entity_id=entity_id,
                knowledge_item_id=item_two_id,
                mention_text="Shared Method",
                status="extracted_candidate",
            ),
            PaperMention(
                id=_id(),
                workspace_id=other_workspace_id,
                paper_id=other_paper_id,
                canonical_entity_id=other_entity_id,
                knowledge_item_id=item_other_id,
                mention_text="Shared Method",
                status="human_confirmed",
            ),
        ]
    )
    evidence_one_id = _id()
    evidence_deleted_id = _id()
    db_session.add_all(
        [
            EvidenceSpan(
                id=evidence_one_id,
                workspace_id=workspace_id,
                knowledge_item_id=item_one_id,
                paper_id=paper_one_id,
                text="The seed paper evaluates Shared Method.",
                relation="supports",
                confidence=0.9,
                is_deleted=False,
            ),
            EvidenceSpan(
                id=evidence_deleted_id,
                workspace_id=workspace_id,
                knowledge_item_id=item_two_id,
                paper_id=paper_two_id,
                text="Deleted evidence must not be returned.",
                relation="supports",
                confidence=1.0,
                is_deleted=True,
            ),
        ]
    )
    db_session.add(
        KnowledgeRelation(
            id=_id(),
            workspace_id=workspace_id,
            source_id=claim_id,
            target_id=item_one_id,
            relation_type="supports",
            confidence=0.8,
        )
    )
    db_session.commit()

    projection = build_bounded_projection(
        db_session,
        workspace_id=workspace_id,
        dense_items=[
            RetrievalResultItem(
                paper_id=paper_one_id,
                chunk_id="seed-chunk",
                text="seed",
                score=0.95,
            )
        ],
        request_id="request-1",
        max_hops=2,
        node_limit=32,
        edge_limit=64,
    )

    assert paper_one_id in projection.supporting_paper_ids
    assert paper_two_id in projection.supporting_paper_ids
    assert other_paper_id not in projection.supporting_paper_ids
    assert evidence_one_id in projection.supporting_evidence_ids
    assert evidence_deleted_id not in projection.supporting_evidence_ids
    assert all(
        claim_id not in path.supporting_item_ids for path in projection.paths
    )
    assert all(
        edge.source in {node.id for node in path.nodes}
        and edge.target in {node.id for node in path.nodes}
        for path in projection.paths
        for edge in path.edges
    )
    assert all(
        node.workspace_id == workspace_id
        for path in projection.paths
        for node in path.nodes
    )
    assert all(
        node.kind in {"chunk", "paper", "canonical_entity"}
        for path in projection.paths
        for node in path.nodes
    )
    assert any(path.evidence for path in projection.paths)


def test_bounded_projection_filters_graph_evidence_by_query_relevance(db_session):
    workspace_id = _id()
    paper_id = _id()
    entity_id = _id()
    item_id = _id()
    distractor_item_id = _id()
    relevant_evidence_id = _id()
    unrelated_evidence_id = _id()
    db_session.add(Workspace(id=workspace_id, name="Query relevance", owner_id="user"))
    db_session.add(Paper(id=paper_id, workspace_id=workspace_id, title="Graph paper"))
    db_session.add(CanonicalEntity(
        id=entity_id,
        workspace_id=workspace_id,
        type="method",
        canonical_name="Graph Method",
        normalization_key="graph method",
        status="human_confirmed",
    ))
    db_session.add(KnowledgeItem(
        id=item_id,
        workspace_id=workspace_id,
        paper_id=paper_id,
        canonical_entity_id=entity_id,
        type="method",
        canonical_name="Graph Method",
        content={"description": "graph method"},
        source_provenance={},
        status="human_confirmed",
    ))
    db_session.add(KnowledgeItem(
        id=distractor_item_id,
        workspace_id=workspace_id,
        paper_id=paper_id,
        canonical_entity_id=entity_id,
        type="method",
        canonical_name="Graph Method",
        content={"description": "distractor"},
        source_provenance={},
        status="human_confirmed",
        confidence=0.99,
    ))
    db_session.add(PaperMention(
        id=_id(),
        workspace_id=workspace_id,
        paper_id=paper_id,
        canonical_entity_id=entity_id,
        knowledge_item_id=item_id,
        mention_text="Graph Method",
        status="human_confirmed",
    ))
    db_session.add_all([
        EvidenceSpan(
            id=relevant_evidence_id,
            workspace_id=workspace_id,
            knowledge_item_id=item_id,
            paper_id=paper_id,
            text="The graph method improves retrieval quality.",
            relation="supports",
            confidence=0.8,
            is_deleted=False,
        ),
        EvidenceSpan(
            id=unrelated_evidence_id,
            workspace_id=workspace_id,
            knowledge_item_id=distractor_item_id,
            paper_id=paper_id,
            text="For a graph classification, Phi(G) is the prediction on G and we simply set Phi_t(G) to be Phi(G).",
            relation="supports",
            confidence=0.99,
            is_deleted=False,
        ),
    ])
    db_session.commit()

    projection = build_bounded_projection(
        db_session,
        workspace_id=workspace_id,
        dense_items=[RetrievalResultItem(
            paper_id=paper_id,
            chunk_id="seed-chunk",
            text=(
                "The graph method improves retrieval quality. "
                "For a graph classification, Phi(G) is the prediction on G "
                "and we simply set Phi_t(G) to be Phi(G)."
            ),
            score=0.95,
        )],
        request_id="query-relevance-1",
        query_text="How does the graph method improve retrieval?",
    )

    assert relevant_evidence_id in projection.supporting_evidence_ids
    assert unrelated_evidence_id not in projection.supporting_evidence_ids
    assert distractor_item_id not in {
        path_item_id
        for path in projection.paths
        for path_item_id in path.supporting_item_ids
    }
    evidence = [item for path in projection.paths for item in path.evidence]
    assert [item.evidence_span_id for item in evidence] == [relevant_evidence_id]
    assert evidence[0].query_relevance_score > 0.15


def test_bounded_projection_packs_global_candidates_and_reports_budget_drops(db_session):
    workspace_id = _id()
    paper_id = _id()
    high_entity_id = "entity-high"
    low_entity_id = "entity-low"
    high_item_id = _id()
    low_item_id = _id()
    high_evidence_id = _id()
    low_evidence_id = _id()
    db_session.add(Workspace(id=workspace_id, name="Global path packing", owner_id="user"))
    db_session.add(Paper(id=paper_id, workspace_id=workspace_id, title="GIP paper"))
    db_session.add_all([
        CanonicalEntity(
            id=high_entity_id,
            workspace_id=workspace_id,
            type="method",
            canonical_name="GIP",
            normalization_key="gip",
        ),
        CanonicalEntity(
            id=low_entity_id,
            workspace_id=workspace_id,
            type="method",
            canonical_name="Other Method",
            normalization_key="other method",
        ),
        KnowledgeItem(
            id=high_item_id,
            workspace_id=workspace_id,
            paper_id=paper_id,
            canonical_entity_id=high_entity_id,
            type="method",
            canonical_name="GIP",
            content={},
            source_provenance={},
        ),
        KnowledgeItem(
            id=low_item_id,
            workspace_id=workspace_id,
            paper_id=paper_id,
            canonical_entity_id=low_entity_id,
            type="method",
            canonical_name="Other Method",
            content={},
            source_provenance={},
        ),
        EvidenceSpan(
            id=high_evidence_id,
            workspace_id=workspace_id,
            knowledge_item_id=high_item_id,
            paper_id=paper_id,
            text="GIP introduces global interactive patterns for graph classification.",
            confidence=0.7,
        ),
        EvidenceSpan(
            id=low_evidence_id,
            workspace_id=workspace_id,
            knowledge_item_id=low_item_id,
            paper_id=paper_id,
            text="GIP is a graph method.",
            confidence=0.99,
        ),
    ])
    db_session.commit()

    projection = build_bounded_projection(
        db_session,
        workspace_id=workspace_id,
        dense_items=[RetrievalResultItem(
            paper_id=paper_id,
            chunk_id="seed-chunk",
            text="GIP introduces global interactive patterns for graph classification.",
            score=0.95,
        )],
        request_id="global-packing-1",
        query_text="GIP global interactive patterns graph classification",
        node_limit=3,
        edge_limit=20,
    )

    assert projection.candidate_path_count == 2
    assert projection.emitted_path_count == len(projection.paths) == 1
    assert projection.dropped_path_count == 1
    assert projection.dropped_path_reasons == {"node_limit": 1}
    assert high_evidence_id in projection.supporting_evidence_ids
    assert low_evidence_id not in projection.supporting_evidence_ids
    assert projection.paths[0].supporting_item_ids == [high_item_id]


def test_graph_evidence_inside_dense_chunk_is_not_automatically_relevant():
    span = SimpleNamespace(
        text="For a graph classification, Phi(G) is the prediction on G and we simply set Phi_t(G) to be Phi(G).",
        paper_id="paper-gip",
        artifact_id=None,
    )
    item = SimpleNamespace(canonical_name="GIP")
    dense = RetrievalResultItem(
        paper_id="paper-gip",
        text=(
            "GIP introduces global interactive patterns for interpretable graph classification. "
            "For a graph classification, Phi(G) is the prediction on G and we simply set Phi_t(G) to be Phi(G)."
        ),
    )

    score = _evidence_relevance_score(
        span,
        item,
        "GIP 的核心思想是什么？用什么方法解决了什么问题？",
        [dense],
    )

    assert score == 0.0


def test_bounded_projection_prioritizes_evidence_before_node_limit(db_session):
    workspace_id = _id()
    paper_id = _id()
    unrelated_entity_id = "entity-a"
    relevant_entity_id = "entity-z"
    unrelated_item_id = _id()
    relevant_item_id = _id()
    unrelated_evidence_id = _id()
    relevant_evidence_id = _id()
    db_session.add(Workspace(id=workspace_id, name="Path priority", owner_id="user"))
    db_session.add(Paper(id=paper_id, workspace_id=workspace_id, title="GIP paper"))
    db_session.add_all([
        CanonicalEntity(
            id=unrelated_entity_id,
            workspace_id=workspace_id,
            type="method",
            canonical_name="Unrelated Method",
            normalization_key="unrelated method",
        ),
        CanonicalEntity(
            id=relevant_entity_id,
            workspace_id=workspace_id,
            type="method",
            canonical_name="GIP",
            normalization_key="gip",
        ),
        KnowledgeItem(
            id=unrelated_item_id,
            workspace_id=workspace_id,
            paper_id=paper_id,
            canonical_entity_id=unrelated_entity_id,
            type="method",
            canonical_name="Unrelated Method",
            content={},
            source_provenance={},
        ),
        KnowledgeItem(
            id=relevant_item_id,
            workspace_id=workspace_id,
            paper_id=paper_id,
            canonical_entity_id=relevant_entity_id,
            type="method",
            canonical_name="GIP",
            content={},
            source_provenance={},
        ),
        EvidenceSpan(
            id=unrelated_evidence_id,
            workspace_id=workspace_id,
            knowledge_item_id=unrelated_item_id,
            paper_id=paper_id,
            text="For a graph classification, Phi(G) is the prediction on G.",
            confidence=0.99,
        ),
        EvidenceSpan(
            id=relevant_evidence_id,
            workspace_id=workspace_id,
            knowledge_item_id=relevant_item_id,
            paper_id=paper_id,
            text="GIP introduces learnable global interactive patterns for graph classification.",
            confidence=0.7,
        ),
    ])
    db_session.commit()

    projection = build_bounded_projection(
        db_session,
        workspace_id=workspace_id,
        dense_items=[RetrievalResultItem(
            paper_id=paper_id,
            chunk_id="seed-chunk",
            text="GIP introduces learnable global interactive patterns for graph classification.",
            score=0.95,
        )],
        request_id="path-priority-1",
        query_text="GIP 的核心思想是什么？用什么方法解决了什么问题？",
        node_limit=5,
        edge_limit=20,
    )

    assert projection.truncated is False
    assert projection.truncation_reason is None
    assert [span.evidence_span_id for path in projection.paths for span in path.evidence] == [
        relevant_evidence_id
    ]
    assert all(path.evidence for path in projection.paths)
    assert unrelated_evidence_id not in projection.supporting_evidence_ids
