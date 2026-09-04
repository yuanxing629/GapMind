"""Knowledge 只读 API 冒烟测试（Phase 1b）。

Knowledge 内容由 Phase 3 的抽取流水线写入，因此 Phase 1b 仅验证端点能以空列表响应，并验证工作区范围约束。
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient

from app.domains.artifact.service import ArtifactService
from app.domains.knowledge.models import (
    CanonicalEntity,
    EvidenceSpan,
    KnowledgeItem,
    KnowledgeRelation,
    PaperMention,
)
from app.domains.paper.models import Paper


def _create_workspace(client: TestClient, name: str = "WS") -> dict:
    resp = client.post("/api/v1/workspaces", json={"name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_list_knowledge_empty(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.get(f"/api/v1/workspaces/{ws['id']}/knowledge")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


def test_list_relations_empty(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.get(f"/api/v1/workspaces/{ws['id']}/knowledge/relations")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_knowledge_graph_empty(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.get(f"/api/v1/workspaces/{ws['id']}/knowledge/graph")
    assert resp.status_code == 200
    assert resp.json()["nodes"] == []
    assert resp.json()["edges"] == []


def test_knowledge_graph_returns_self_contained_nodes_and_edges(
    client: TestClient,
    db_session,
) -> None:
    ws = _create_workspace(client)
    source = KnowledgeItem(
        workspace_id=ws["id"],
        paper_id=None,
        type="method",
        canonical_name="Method A",
        content={"description": "A"},
        source_provenance={},
        created_by="agent",
        confidence=0.9,
        status="extracted_candidate",
        is_deleted=False,
    )
    target = KnowledgeItem(
        workspace_id=ws["id"],
        paper_id=None,
        type="dataset",
        canonical_name="Dataset B",
        content={"description": "B"},
        source_provenance={},
        created_by="agent",
        confidence=0.8,
        status="extracted_candidate",
        is_deleted=False,
    )
    db_session.add_all([source, target])
    db_session.flush()
    db_session.add(
        KnowledgeRelation(
            workspace_id=ws["id"],
            source_id=source.id,
            target_id=target.id,
            relation_type="evaluates_on",
            confidence=0.75,
            payload={},
            is_deleted=False,
        )
    )
    db_session.commit()

    resp = client.get(f"/api/v1/workspaces/{ws['id']}/knowledge/graph")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {node["label"] for node in body["nodes"]} == {"Method A", "Dataset B"}
    assert body["edges"][0]["source"] == source.id
    assert body["edges"][0]["target"] == target.id


def test_get_knowledge_item_not_found(client: TestClient) -> None:
    ws = _create_workspace(client)
    resp = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/00000000-0000-0000-0000-000000000000"
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "knowledge_item_not_found"


def test_knowledge_workspace_not_found(client: TestClient) -> None:
    resp = client.get("/api/v1/workspaces/00000000-0000-0000-0000-000000000000/knowledge")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "workspace_not_found"


def test_knowledge_item_review_confirm_and_edit(client: TestClient, db_session) -> None:
    ws = _create_workspace(client)
    item = KnowledgeItem(
        workspace_id=ws["id"], type="claim", canonical_name="Old claim",
        content={"statement": "old"}, source_provenance={}, created_by="agent",
        confidence=0.5, status="extracted_candidate", is_deleted=False,
    )
    db_session.add(item)
    db_session.commit()

    response = client.patch(
        f"/api/v1/workspaces/{ws['id']}/knowledge/{item.id}/review",
        json={"action": "edit", "canonical_name": "Reviewed claim", "content": {"statement": "new"}, "note": "checked"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["canonical_name"] == "Reviewed claim"
    assert body["content"]["statement"] == "new"
    assert body["status"] == "human_confirmed"
    assert body["review_note"] == "checked"


def test_evidence_context_and_markdown_download(client: TestClient, db_session) -> None:
    ws = _create_workspace(client)
    artifact = ArtifactService(db_session).save_upload(
        workspace_id=ws["id"], filename="paper.md", content=b"# Intro\nEvidence sentence.",
        mime_type="text/markdown", kind="parsed_markdown",
    )
    paper = Paper(
        workspace_id=ws["id"], title="Evidence paper", authors=[], source="manual",
        parsed_markdown_artifact_id=artifact.id, parse_status="parsed", is_deleted=False,
    )
    item = KnowledgeItem(
        workspace_id=ws["id"], paper_id=paper.id, type="claim", canonical_name="Claim",
        content={"statement": "Evidence sentence."}, source_provenance={}, created_by="agent",
        confidence=0.8, status="extracted_candidate", is_deleted=False,
    )
    db_session.add_all([paper, item])
    db_session.flush()
    db_session.add(EvidenceSpan(
        workspace_id=ws["id"], knowledge_item_id=item.id, paper_id=paper.id,
        artifact_id=artifact.id, artifact_kind="parsed_markdown", artifact_version="v1",
        start_char=7, end_char=24, text="Evidence sentence.", relation="supports", confidence=0.8,
    ))
    db_session.commit()

    context = client.get(f"/api/v1/workspaces/{ws['id']}/knowledge/{item.id}/evidence/context")
    assert context.status_code == 200, context.text
    assert context.json()["content"] == "# Intro\nEvidence sentence."
    assert context.json()["spans"][0]["start_char"] == 7

    download = client.get(f"/api/v1/workspaces/{ws['id']}/artifacts/{artifact.id}/download")
    assert download.status_code == 200
    assert download.content == b"# Intro\nEvidence sentence."


def test_evidence_context_binds_selected_span_to_its_artifact(
    client: TestClient, db_session
) -> None:
    ws = _create_workspace(client, "Exact evidence context")
    artifact_one = ArtifactService(db_session).save_upload(
        workspace_id=ws["id"], filename="paper-v1.md", content=b"Version one evidence.",
        mime_type="text/markdown", kind="parsed_markdown",
    )
    artifact_two = ArtifactService(db_session).save_upload(
        workspace_id=ws["id"], filename="paper-v2.md", content=b"Version two evidence.",
        mime_type="text/markdown", kind="parsed_markdown",
    )
    paper = Paper(
        workspace_id=ws["id"], title="Versioned evidence", authors=[], source="manual",
        parsed_markdown_artifact_id=artifact_one.id, parse_status="parsed", is_deleted=False,
    )
    item = KnowledgeItem(
        workspace_id=ws["id"], paper_id=paper.id, type="claim", canonical_name="Claim",
        content={"statement": "Versioned evidence."}, source_provenance={}, created_by="agent",
        confidence=0.8, status="extracted_candidate", is_deleted=False,
    )
    db_session.add_all([paper, item])
    db_session.flush()
    item.paper_id = paper.id
    span_one = EvidenceSpan(
        id=str(uuid4()), workspace_id=ws["id"], knowledge_item_id=item.id, paper_id=paper.id,
        artifact_id=artifact_one.id, artifact_kind="parsed_markdown", artifact_version="v1",
        start_char=0, end_char=21, text="Version one evidence.", relation="supports", confidence=0.8,
    )
    span_two = EvidenceSpan(
        id=str(uuid4()), workspace_id=ws["id"], knowledge_item_id=item.id, paper_id=paper.id,
        artifact_id=artifact_two.id, artifact_kind="parsed_markdown", artifact_version="v2",
        start_char=0, end_char=21, text="Version two evidence.", relation="supports", confidence=0.8,
    )
    db_session.add_all([span_one, span_two])
    db_session.commit()

    context = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/{item.id}/evidence/context"
        f"?evidence_span_id={span_two.id}"
    )

    assert context.status_code == 200, context.text
    body = context.json()
    assert body["artifact_id"] == artifact_two.id
    assert body["content"] == "Version two evidence."
    assert [span["id"] for span in body["spans"]] == [span_two.id]


def test_graph_contains_layered_nodes_and_expands_entity_neighbors(
    client: TestClient, db_session
) -> None:
    ws = _create_workspace(client)
    paper = Paper(
        workspace_id=ws["id"], title="Layered paper", authors=[], source="manual", is_deleted=False,
    )
    entity = CanonicalEntity(
        workspace_id=ws["id"], type="method", canonical_name="Method A",
        normalization_key="methoda", aliases=[], status="extracted_candidate", is_deleted=False,
    )
    db_session.add_all([paper, entity])
    db_session.flush()
    item = KnowledgeItem(
        workspace_id=ws["id"], paper_id=paper.id, canonical_entity_id=entity.id,
        type="method", canonical_name="Method A", content={"description": "A"},
        source_provenance={}, created_by="agent", confidence=0.9,
        status="extracted_candidate", is_deleted=False,
    )
    db_session.add(item)
    db_session.flush()
    db_session.add(PaperMention(
        workspace_id=ws["id"], paper_id=paper.id, canonical_entity_id=entity.id,
        knowledge_item_id=item.id, mention_text="Method A", start_char=0, end_char=8,
        confidence=0.9, status="extracted_candidate", is_deleted=False,
    ))
    db_session.commit()

    graph = client.get(f"/api/v1/workspaces/{ws['id']}/knowledge/graph?limit=20")
    assert graph.status_code == 200, graph.text
    assert {node["node_kind"] for node in graph.json()["nodes"]} >= {
        "knowledge", "paper", "canonical_entity", "paper_mention"
    }

    landscape = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph?projection_mode=landscape&limit=20"
    )
    assert landscape.status_code == 200, landscape.text
    assert "paper_mention" not in {
        node["node_kind"] for node in landscape.json()["nodes"]
    }

    evidence = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph?projection_mode=evidence&limit=20"
    )
    assert evidence.status_code == 200, evidence.text
    assert "paper_mention" in {
        node["node_kind"] for node in evidence.json()["nodes"]
    }

    neighbors = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph/neighbors/entity:{entity.id}"
    )
    assert neighbors.status_code == 200, neighbors.text
    assert any(node["id"] == item.id for node in neighbors.json()["nodes"])


def test_graph_projection_modes_and_append_metadata(client: TestClient, db_session) -> None:
    ws = _create_workspace(client, "Projection modes")
    paper = Paper(
        workspace_id=ws["id"], title="Graph paper", authors=[], source="manual",
        parse_status="parsed", is_deleted=False,
    )
    db_session.add(paper)
    db_session.flush()
    items = [
        KnowledgeItem(
            workspace_id=ws["id"], paper_id=paper.id, type=kind,
            canonical_name=name, content={"statement": name}, source_provenance={},
            created_by="agent", confidence=confidence,
            status="human_confirmed" if kind == "claim" else "extracted_candidate",
            is_deleted=False,
        )
        for kind, name, confidence in [
            ("method", "Method node", 0.95),
            ("task", "Task node", 0.90),
            ("claim", "Claim node", 0.85),
            ("limitation", "Limitation node", 0.80),
        ]
    ]
    db_session.add_all(items)
    db_session.flush()
    db_session.add(KnowledgeRelation(
        workspace_id=ws["id"], source_id=items[2].id, target_id=items[3].id,
        relation_type="qualifies", confidence=0.8, payload={}, is_deleted=False,
    ))
    db_session.commit()

    landscape = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph",
        params={"projection_mode": "landscape", "limit": 1},
    )
    assert landscape.status_code == 200, landscape.text
    landscape_body = landscape.json()
    assert landscape_body["projection_mode"] == "landscape"
    assert landscape_body["has_more"] is True
    assert landscape_body["workspace_counts"]["knowledge_items"] == 4
    assert landscape_body["workspace_counts"]["parsed_papers"] == 1
    assert landscape_body["node_counts"]["method"] == 1
    assert landscape_body["node_counts"]["task"] == 1
    assert "claim" not in landscape_body["node_counts"]

    second_batch = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph",
        params={"projection_mode": "landscape", "limit": 1, "offset": 1},
    )
    assert second_batch.status_code == 200, second_batch.text
    assert second_batch.json()["has_more"] is False
    first_ids = {node["id"] for node in landscape_body["nodes"] if node["node_kind"] == "knowledge"}
    second_ids = {node["id"] for node in second_batch.json()["nodes"] if node["node_kind"] == "knowledge"}
    assert first_ids.isdisjoint(second_ids)

    claims = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph",
        params={"projection_mode": "claims", "limit": 20},
    )
    assert claims.status_code == 200, claims.text
    claim_types = {
        node["type"] for node in claims.json()["nodes"] if node["node_kind"] == "knowledge"
    }
    assert claim_types == {"claim", "limitation"}
    assert claims.json()["relation_counts"]["qualifies"] == 1
    assert all(edge["source_label"] and edge["target_label"] for edge in claims.json()["edges"])


def test_graph_search_finds_unloaded_nodes_without_crossing_workspace(
    client: TestClient, db_session
) -> None:
    ws = _create_workspace(client, "Search graph")
    other = _create_workspace(client, "Other graph")
    db_session.add_all([
        KnowledgeItem(
            workspace_id=ws["id"], type="method", canonical_name="Rare Transformer",
            content={}, source_provenance={}, created_by="agent", confidence=0.7,
            status="extracted_candidate", is_deleted=False,
        ),
        KnowledgeItem(
            workspace_id=other["id"], type="method", canonical_name="Rare Private Method",
            content={}, source_provenance={}, created_by="agent", confidence=0.99,
            status="extracted_candidate", is_deleted=False,
        ),
    ])
    db_session.commit()

    response = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph/search",
        params={"q": "Rare", "projection_mode": "landscape"},
    )
    assert response.status_code == 200, response.text
    assert [item["label"] for item in response.json()["items"]] == ["Rare Transformer"]


def test_graph_edges_never_reference_missing_nodes(client: TestClient, db_session) -> None:
    ws = _create_workspace(client, "Self contained")
    items = [
        KnowledgeItem(
            workspace_id=ws["id"], type="claim", canonical_name=f"Claim {index}",
            content={}, source_provenance={}, created_by="agent",
            confidence=0.9 - index * 0.1, status="extracted_candidate", is_deleted=False,
        )
        for index in range(3)
    ]
    db_session.add_all(items)
    db_session.flush()
    db_session.add_all([
        KnowledgeRelation(
            workspace_id=ws["id"], source_id=items[0].id, target_id=items[1].id,
            relation_type="supports", confidence=0.8, payload={}, is_deleted=False,
        ),
        KnowledgeRelation(
            workspace_id=ws["id"], source_id=items[1].id, target_id=items[2].id,
            relation_type="supports", confidence=0.7, payload={}, is_deleted=False,
        ),
    ])
    db_session.commit()

    response = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph",
        params={"projection_mode": "claims", "limit": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    node_ids = {node["id"] for node in body["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in body["edges"])


def test_workspace_graph_aggregates_shared_entities_and_relations(
    client: TestClient, db_session
) -> None:
    ws = _create_workspace(client, "Workspace projection")
    papers = [
        Paper(workspace_id=ws["id"], title=f"Paper {index}", authors=[], source="manual", is_deleted=False)
        for index in (1, 2)
    ]
    method = CanonicalEntity(
        workspace_id=ws["id"], type="method", canonical_name="Shared Method",
        normalization_key="sharedmethod", aliases=["SM"], status="extracted_candidate", is_deleted=False,
    )
    dataset = CanonicalEntity(
        workspace_id=ws["id"], type="dataset", canonical_name="Shared Dataset",
        normalization_key="shareddataset", aliases=[], status="extracted_candidate", is_deleted=False,
    )
    db_session.add_all([*papers, method, dataset])
    db_session.flush()
    items = []
    for index, paper in enumerate(papers):
        method_item = KnowledgeItem(
            workspace_id=ws["id"], paper_id=paper.id, canonical_entity_id=method.id,
            type="method", canonical_name="Shared Method", content={}, source_provenance={},
            created_by="agent", confidence=0.8 + index * 0.1,
            status="human_confirmed" if index == 0 else "extracted_candidate", is_deleted=False,
        )
        dataset_item = KnowledgeItem(
            workspace_id=ws["id"], paper_id=paper.id, canonical_entity_id=dataset.id,
            type="dataset", canonical_name="Shared Dataset", content={}, source_provenance={},
            created_by="agent", confidence=0.7, status="extracted_candidate", is_deleted=False,
        )
        items.extend([method_item, dataset_item])
    db_session.add_all(items)
    db_session.flush()
    db_session.add_all([
        PaperMention(
            workspace_id=ws["id"], paper_id=paper.id, canonical_entity_id=entity.id,
            knowledge_item_id=item.id, mention_text=entity.canonical_name,
            start_char=0, end_char=10, confidence=item.confidence, status="extracted_candidate", is_deleted=False,
        )
        for paper, entity, item in [
            (papers[0], method, items[0]), (papers[1], method, items[2]),
            (papers[0], dataset, items[1]), (papers[1], dataset, items[3]),
        ]
    ])
    db_session.add_all([
        EvidenceSpan(
            workspace_id=ws["id"], knowledge_item_id=item.id, paper_id=item.paper_id,
            artifact_id=None, artifact_kind="parsed_markdown", artifact_version="v1",
            start_char=0, end_char=10, text=item.canonical_name, relation="supports", confidence=item.confidence,
        )
        for item in items
    ])
    db_session.flush()
    db_session.add_all([
        KnowledgeRelation(
            workspace_id=ws["id"], source_id=items[index].id, target_id=items[index + 1].id,
            relation_type="evaluates_on", confidence=0.6 + index * 0.1, payload={}, is_deleted=False,
        )
        for index in (0, 2)
    ])
    db_session.commit()

    response = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph",
        params={"projection_mode": "workspace", "limit": 20},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    entity_nodes = {
        node["label"]: node for node in body["nodes"] if node["node_kind"] == "canonical_entity"
    }
    assert set(entity_nodes) == {"Shared Method", "Shared Dataset"}
    assert entity_nodes["Shared Method"]["paper_count"] == 2
    assert entity_nodes["Shared Method"]["mention_count"] == 2
    assert entity_nodes["Shared Method"]["knowledge_item_count"] == 2
    assert entity_nodes["Shared Method"]["evidence_count"] == 2
    assert entity_nodes["Shared Method"]["confirmed_item_count"] == 1
    assert entity_nodes["Shared Method"]["aliases"] == ["SM"]
    assert set(entity_nodes["Shared Method"]["supporting_paper_ids"]) == {papers[0].id, papers[1].id}

    relation = next(edge for edge in body["edges"] if edge["relation_type"] == "evaluates_on")
    assert relation["occurrence_count"] == 2
    assert relation["paper_count"] == 2
    assert set(relation["supporting_paper_ids"]) == {papers[0].id, papers[1].id}
    assert len(relation["supporting_item_ids"]) == 4
    node_ids = {node["id"] for node in body["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in body["edges"])
    assert body["node_counts"] == {"paper": 2, "canonical_entity": 2}

    search = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph/search",
        params={"q": "Shared Method", "projection_mode": "workspace"},
    )
    assert search.status_code == 200, search.text
    assert search.json()["items"][0]["node_id"] == f"entity:{method.id}"
    focused = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph/neighbors/entity:{method.id}",
        params={"projection_mode": "workspace", "limit": 20},
    )
    assert focused.status_code == 200, focused.text
    assert f"entity:{method.id}" in {node["id"] for node in focused.json()["nodes"]}
    assert all(node["node_kind"] in {"paper", "canonical_entity"} for node in focused.json()["nodes"])


def test_workspace_graph_keeps_same_name_types_and_claims_paper_local(
    client: TestClient, db_session
) -> None:
    ws = _create_workspace(client, "Entity type boundaries")
    papers = [
        Paper(workspace_id=ws["id"], title=f"Boundary paper {index}", authors=[], source="manual", is_deleted=False)
        for index in (1, 2)
    ]
    method = CanonicalEntity(
        workspace_id=ws["id"], type="method", canonical_name="Shared Name",
        normalization_key="sharedname", aliases=[], status="extracted_candidate", is_deleted=False,
    )
    task = CanonicalEntity(
        workspace_id=ws["id"], type="task", canonical_name="Shared Name",
        normalization_key="sharedname", aliases=[], status="extracted_candidate", is_deleted=False,
    )
    db_session.add_all([*papers, method, task])
    db_session.flush()
    db_session.add_all([
        KnowledgeItem(
            workspace_id=ws["id"], paper_id=papers[0].id, canonical_entity_id=method.id,
            type="method", canonical_name="Shared Name", content={}, source_provenance={},
            created_by="agent", confidence=0.8, status="extracted_candidate", is_deleted=False,
        ),
        KnowledgeItem(
            workspace_id=ws["id"], paper_id=papers[1].id, canonical_entity_id=task.id,
            type="task", canonical_name="Shared Name", content={}, source_provenance={},
            created_by="agent", confidence=0.8, status="extracted_candidate", is_deleted=False,
        ),
        KnowledgeItem(
            workspace_id=ws["id"], paper_id=papers[0].id, type="claim", canonical_name="Same claim",
            content={}, source_provenance={}, created_by="agent", confidence=0.8,
            status="extracted_candidate", is_deleted=False,
        ),
        KnowledgeItem(
            workspace_id=ws["id"], paper_id=papers[1].id, type="claim", canonical_name="Same claim",
            content={}, source_provenance={}, created_by="agent", confidence=0.8,
            status="extracted_candidate", is_deleted=False,
        ),
    ])
    db_session.commit()

    response = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph",
        params={"projection_mode": "workspace", "limit": 20},
    )
    assert response.status_code == 200, response.text
    nodes = response.json()["nodes"]
    entities = [node for node in nodes if node["node_kind"] == "canonical_entity"]
    assert {(node["label"], node["entity_type"]) for node in entities} == {
        ("Shared Name", "method"), ("Shared Name", "task")
    }
    assert all(node["node_kind"] != "knowledge" for node in nodes)


def test_graph_paper_filter_is_strict_unless_related_mode_is_explicit(
    client: TestClient, db_session
) -> None:
    ws = _create_workspace(client, "Strict paper filter")
    papers = [
        Paper(workspace_id=ws["id"], title=f"Strict paper {index}", authors=[], source="manual", is_deleted=False)
        for index in (1, 2)
    ]
    entity = CanonicalEntity(
        workspace_id=ws["id"], type="method", canonical_name="Shared strict method",
        normalization_key="sharedstrictmethod", aliases=[], status="extracted_candidate", is_deleted=False,
    )
    db_session.add_all([*papers, entity])
    db_session.flush()
    items = [
        KnowledgeItem(
            workspace_id=ws["id"], paper_id=paper.id, canonical_entity_id=entity.id,
            type="method", canonical_name="Shared strict method", content={}, source_provenance={},
            created_by="agent", confidence=0.8, status="extracted_candidate", is_deleted=False,
        )
        for paper in papers
    ]
    db_session.add_all(items)
    db_session.flush()
    db_session.add_all([
        PaperMention(
            workspace_id=ws["id"], paper_id=paper.id, canonical_entity_id=entity.id,
            knowledge_item_id=item.id, mention_text="Shared strict method", start_char=0, end_char=10,
            confidence=0.8, status="extracted_candidate", is_deleted=False,
        )
        for paper, item in zip(papers, items, strict=False)
    ])
    db_session.commit()

    strict = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph",
        params={"projection_mode": "evidence", "paper_id": papers[0].id, "limit": 20},
    )
    assert strict.status_code == 200, strict.text
    strict_mentions = [node for node in strict.json()["nodes"] if node["node_kind"] == "paper_mention"]
    assert {node["paper_id"] for node in strict_mentions} == {papers[0].id}

    related = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph",
        params={
            "projection_mode": "evidence", "paper_id": papers[0].id,
            "include_related_papers": True, "limit": 20,
        },
    )
    assert related.status_code == 200, related.text
    related_mentions = [node for node in related.json()["nodes"] if node["node_kind"] == "paper_mention"]
    assert {node["paper_id"] for node in related_mentions} == {papers[0].id, papers[1].id}


def test_workspace_graph_isolates_workspaces_and_reports_truncation(
    client: TestClient, db_session
) -> None:
    ws = _create_workspace(client, "Visible workspace")
    other = _create_workspace(client, "Private workspace")
    entities = []
    for workspace_id, label in ((ws["id"], "Visible method"), (other["id"], "Private method")):
        entity = CanonicalEntity(
            workspace_id=workspace_id, type="method", canonical_name=label,
            normalization_key=label.replace(" ", "").lower(), aliases=[],
            status="extracted_candidate", is_deleted=False,
        )
        paper = Paper(workspace_id=workspace_id, title=label, authors=[], source="manual", is_deleted=False)
        db_session.add_all([entity, paper])
        db_session.flush()
        db_session.add(KnowledgeItem(
            workspace_id=workspace_id, paper_id=paper.id, canonical_entity_id=entity.id,
            type="method", canonical_name=label, content={}, source_provenance={}, created_by="agent",
            confidence=0.9, status="extracted_candidate", is_deleted=False,
        ))
        entities.append(entity)
    db_session.commit()

    response = client.get(
        f"/api/v1/workspaces/{ws['id']}/knowledge/graph",
        params={"projection_mode": "workspace", "limit": 1, "edge_limit": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert all("Private" not in node["label"] for node in body["nodes"])
    assert body["has_more"] is True
    assert body["truncated"] is True
    assert body["truncation_reason"] == "node_limit"
    node_ids = {node["id"] for node in body["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in body["edges"])
