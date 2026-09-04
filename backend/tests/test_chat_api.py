"""Chat API 测试使用 fake gateway，绝不调用外部 LLM。"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.config import settings
from app.domains.chat.service import ChatService
from app.domains.knowledge.graphrag import BoundedGraphProjection
from app.domains.knowledge.schemas import GraphRAGPathRead
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem


@dataclass
class FakeResponse:
    content: str
    model: str = "fake-remote"
    prompt_tokens: int = 10
    completion_tokens: int = 5
    total_tokens: int = 15


class FakeGateway:
    api_key = "test-key"
    vision_model = "fake-vision"

    def __init__(self, content: str = "这是 AI 的回答") -> None:
        self.content = content
        self.chat_contents: list[str] = []
        self.calls: list[list[dict[str, str]]] = []
        self.call_kwargs: list[dict] = []
        self.stream_calls: list[list[dict[str, str]]] = []
        self.stream_call_kwargs: list[dict] = []
        self.fail = False

    def chat_completion(self, messages, **kwargs):
        self.calls.append(messages)
        self.call_kwargs.append(kwargs)
        if self.fail:
            raise RuntimeError("upstream unavailable")
        content = self.chat_contents.pop(0) if self.chat_contents else self.content
        return FakeResponse(content)

    def stream_chat_completion(self, messages, **kwargs):
        self.stream_calls.append(messages)
        self.stream_call_kwargs.append(kwargs)
        for delta in getattr(self, "stream_deltas", ["流式"]):
            yield delta


def test_image_chat_accepts_a_vision_only_api_key() -> None:
    ChatService._ensure_llm_credentials(
        SimpleNamespace(api_key="", vision_api_key="vision-key"),
        ["data:image/png;base64,abc"],
    )


@pytest.fixture
def fake_gateway(monkeypatch):
    gateway = FakeGateway()
    monkeypatch.setattr("app.domains.chat.service.get_llm_gateway", lambda: gateway)
    return gateway


def _confirmed_plan(db_session, workspace_id: str, title: str):
    from app.domains.discover.models import ResearchOpportunity, ResearchPlan

    opportunity = ResearchOpportunity(
        workspace_id=workspace_id,
        title=title,
        summary="已确认研究机会",
        rationale="测试用机会",
        status="confirmed",
    )
    db_session.add(opportunity)
    db_session.flush()
    plan = ResearchPlan(
        workspace_id=workspace_id,
        opportunity_id=opportunity.id,
        status="draft",
        title=title,
        research_question="该方法相对 ProtGNN 是否提升鲁棒性？",
        hypothesis="拓扑约束可以提升 OOD 性能。",
        scope_and_assumptions="节点分类",
        datasets=["Cora"],
        baselines=["ProtGNN"],
        metrics=["accuracy"],
        validation_steps=["比较基线"],
        expected_supporting_result="OOD accuracy 提升",
        falsification_criteria="没有稳定提升",
        risks=[],
        resource_constraints="单张 GPU",
    )
    db_session.add(plan)
    db_session.commit()
    return plan


def test_first_send_creates_conversation_and_two_messages(client, fake_gateway):
    response = client.post(
        "/api/v1/chat/conversations/send", json={"content": "  什么是时间图神经网络？  "}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation"]["title"] == "什么是时间图神经网络？"
    assert body["user_message"]["role"] == "user"
    assert body["assistant_message"]["status"] == "completed"
    assert body["assistant_message"]["model"] == "fake-remote"
    assert body["assistant_message"]["total_tokens"] == 15
    assert body["assistant_message"]["prompt_chars"] == len("什么是时间图神经网络？")
    assert body["assistant_message"]["response_chars"] == len("这是 AI 的回答")
    assert body["assistant_message"]["first_token_latency_ms"] is None
    assert body["assistant_message"]["completion_latency_ms"] >= 0
    assert len(fake_gateway.calls) == 1
    assert fake_gateway.call_kwargs[-1]["disable_thinking"] is True

    detail = client.get(f"/api/v1/chat/conversations/{body['conversation']['id']}").json()
    assert [item["role"] for item in detail["messages"]] == ["user", "assistant"]


def _test_png_data_url() -> str:
    return "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nimage").decode("ascii")


def test_image_send_uses_vision_model_and_protected_attachment(
    client, fake_gateway, monkeypatch, tmp_path
):
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path))
    response = client.post(
        "/api/v1/chat/conversations/send",
        json={
            "content": "请解释这张图",
            "images": [
                {
                    "filename": "figure.png",
                    "mime_type": "image/png",
                    "data_url": _test_png_data_url(),
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    image = body["user_message"]["images"][0]
    assert image["filename"] == "figure.png"
    assert image["mime_type"] == "image/png"
    assert fake_gateway.call_kwargs[-1]["model_override"] == "fake-vision"
    assert fake_gateway.calls[-1][-1]["content"][0] == {
        "type": "text",
        "text": "请解释这张图",
    }
    assert fake_gateway.calls[-1][-1]["content"][1]["type"] == "image_url"

    image_response = client.get(
        f"/api/v1/chat/conversations/{body['conversation']['id']}"
    )
    assert image_response.status_code == 200
    persisted_image = image_response.json()["messages"][0]["images"][0]
    served = client.get(
        f"/api/v1/chat/conversations/{body['conversation']['id']}"
        f"/messages/{body['user_message']['id']}/images/{persisted_image['id']}"
    )
    assert served.status_code == 200
    assert served.headers["content-type"].startswith("image/png")
    assert served.content == b"\x89PNG\r\n\x1a\nimage"


def test_stream_image_uses_vision_model(client, fake_gateway, monkeypatch, tmp_path):
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_storage_dir", str(tmp_path))
    conversation = client.post(
        "/api/v1/chat/conversations", json={"title": "图片对话"}
    ).json()
    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages/stream",
        json={
            "content": "继续分析这张图",
            "images": [
                {
                    "filename": "chart.png",
                    "mime_type": "image/png",
                    "data_url": _test_png_data_url(),
                }
            ],
        },
    )

    assert response.status_code == 200
    assert "data:" in response.text
    assert fake_gateway.stream_call_kwargs[-1]["model_override"] == "fake-vision"
    detail = client.get(
        f"/api/v1/chat/conversations/{conversation['id']}"
    ).json()
    assert detail["messages"][0]["images"][0]["filename"] == "chart.png"


def test_existing_send_includes_completed_history(client, fake_gateway):
    first = client.post("/api/v1/chat/conversations/send", json={"content": "先解释 GNN"}).json()
    client.post(
        f"/api/v1/chat/conversations/{first['conversation']['id']}/messages",
        json={"content": "再解释消息传递"},
    )

    second_context = fake_gateway.calls[-1]
    assert second_context == [
        {"role": "user", "content": "先解释 GNN"},
        {"role": "assistant", "content": "这是 AI 的回答"},
        {"role": "user", "content": "再解释消息传递"},
    ]


def test_chat_history_budget_prefers_newest_completed_messages(db_session, monkeypatch):
    from app.core.config import settings
    from app.domains.chat.service import ChatService

    monkeypatch.setattr(settings, "chat_history_char_limit", 6)
    messages = [
        SimpleNamespace(role="user", status="completed", content="older!"),
        SimpleNamespace(role="assistant", status="completed", content="newest"),
    ]

    context = ChatService(db_session)._build_context(messages, "current question")

    assert context == [
        {"role": "assistant", "content": "newest"},
        {"role": "user", "content": "current question"},
    ]


def test_prompt_budget_keeps_current_question_and_newest_history(db_session, monkeypatch):
    from app.core.config import settings
    from app.domains.chat.service import ChatService

    monkeypatch.setattr(settings, "chat_prompt_max_context_chars", 44)
    system = {"role": "system", "content": "system context"}
    context = [
        {"role": "user", "content": "older history that does not fit"},
        {"role": "assistant", "content": "newest history"},
        {"role": "user", "content": "current question"},
    ]

    messages = ChatService(db_session)._budget_prompt_messages(system, context)

    assert messages == [
        system,
        {"role": "assistant", "content": "newest history"},
        {"role": "user", "content": "current question"},
    ]
    assert sum(len(message["content"]) for message in messages) <= 44


def test_conversation_search_rename_and_soft_delete(client, fake_gateway):
    first = client.post("/api/v1/chat/conversations/send", json={"content": "研究时间图"}).json()
    second = client.post("/api/v1/chat/conversations/send", json={"content": "研究知识图谱"}).json()

    search = client.get("/api/v1/chat/conversations", params={"query": "时间"})
    assert search.status_code == 200
    assert search.json()["total"] == 1
    assert search.json()["items"][0]["id"] == first["conversation"]["id"]

    renamed = client.patch(
        f"/api/v1/chat/conversations/{second['conversation']['id']}",
        json={"title": "新的知识图谱对话"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "新的知识图谱对话"

    assert (
        client.delete(f"/api/v1/chat/conversations/{first['conversation']['id']}").json()["deleted"]
        is True
    )
    assert (
        client.get(f"/api/v1/chat/conversations/{first['conversation']['id']}").status_code == 404
    )
    assert (
        client.post(
            f"/api/v1/chat/conversations/{first['conversation']['id']}/messages",
            json={"content": "不能继续"},
        ).status_code
        == 404
    )


def test_failed_answer_is_persisted_and_can_be_retried(client, fake_gateway):
    fake_gateway.fail = True
    response = client.post("/api/v1/chat/conversations/send", json={"content": "测试失败恢复"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["conversation_id"]
    assert detail["assistant_message_id"]

    conversation_id = detail["conversation_id"]
    assistant_id = detail["assistant_message_id"]
    messages = client.get(f"/api/v1/chat/conversations/{conversation_id}").json()["messages"]
    assert messages[-1]["status"] == "failed"
    assert messages[-1]["content"] == ""

    fake_gateway.fail = False
    retry = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages/{assistant_id}/retry"
    )
    assert retry.status_code == 200
    assert retry.json()["assistant_message"]["status"] == "completed"
    assert len(fake_gateway.calls) == 2

    assert (
        client.post(
            f"/api/v1/chat/conversations/{conversation_id}/messages/{assistant_id}/retry"
        ).status_code
        == 409
    )


def test_missing_api_key_is_mapped_to_503_and_persisted(client, fake_gateway):
    fake_gateway.api_key = ""
    response = client.post("/api/v1/chat/conversations/send", json={"content": "测试未配置密钥"})
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "llm_unavailable"
    messages = client.get(f"/api/v1/chat/conversations/{detail['conversation_id']}").json()[
        "messages"
    ]
    assert messages[-1]["status"] == "failed"


def test_validation_and_generating_conflict(client, db_session, fake_gateway):
    assert (
        client.post("/api/v1/chat/conversations/send", json={"content": "   "}).status_code == 422
    )
    assert (
        client.post("/api/v1/chat/conversations/send", json={"content": "x" * 12001}).status_code
        == 400
    )

    created = client.post("/api/v1/chat/conversations", json={}).json()
# 通过公开 model fixture 路径插入真实的 generating 消息。
    from app.db.models import ChatMessage

    db_session.add(
        ChatMessage(
            conversation_id=created["id"],
            role="assistant",
            content="",
            status="generating",
            sequence=1,
        )
    )
    db_session.commit()

    response = client.post(
        f"/api/v1/chat/conversations/{created['id']}/messages",
        json={"content": "重复发送"},
    )
    assert response.status_code == 409


def test_workspace_chat_retrieves_persists_citations_and_opens_source(
    client,
    db_session,
    fake_gateway,
    monkeypatch,
):
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "图学习", "topic": "图神经网络解释"},
    ).json()
    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "Interpretable Graph Models", "authors": [], "year": 2024},
    ).json()

    from app.domains.artifact.service import ArtifactService

    source_text = "Intro Evidence about graph explanations and robust evaluation."
    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace["id"],
        filename="paper.txt",
        content=source_text.encode("utf-8"),
        mime_type="text/plain",
        kind="parsed_text",
    )

    def fake_search(**kwargs):
        assert kwargs["workspace_id"] == workspace["id"]
        assert kwargs["use_reranker"] is True
        assert kwargs["diversify_by_paper"] is True
        return RetrievalResponse(
            workspace_id=workspace["id"],
            query=kwargs["query"],
            items=[
                RetrievalResultItem(
                    paper_id=paper["id"],
                    artifact_id=artifact.id,
                    chunk_id="chunk-1",
                    section="Methods",
                    text="Evidence about graph\x00 explanations and robust evaluation.",
                    score=0.91,
                    retrieval_stage="reranked",
                )
            ],
            total=1,
            request_id="retrieval-test-001",
            latency_ms=12.5,
            filters_applied={
                "recall_count": 3,
                "reranker_enabled": True,
                "reranker_applied": True,
            },
        )

    monkeypatch.setattr("app.domains.chat.service.semantic_search", fake_search)
    monkeypatch.setattr(
        "app.domains.chat.service.find_chunk_record",
        lambda *_, **__: SimpleNamespace(
            source_artifact_id=artifact.id,
            start_char=6,
            end_char=source_text.index(".") + 1,
        ),
    )
    fake_gateway.content = "该论文强调了稳健评估。[E1]"

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={"content": "这个工作区如何评估解释方法？", "workspace_id": workspace["id"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation"]["workspace_id"] == workspace["id"]
    assistant = body["assistant_message"]
    assert assistant["grounding_status"] == "grounded"
    retrieval_audit = assistant["retrieval_audit"]
    assert {
        key: value for key, value in retrieval_audit.items() if key != "graph"
    } == {
        "request_id": "retrieval-test-001",
        "status": "succeeded",
        "diagnostic_code": None,
        "recall_count": 3,
        "returned_chunk_count": 1,
        "final_paper_count": 1,
        "latency_ms": 12.5,
        "reranker_status": "applied",
    }
    graph_audit = retrieval_audit["graph"]
    assert {
        key: value for key, value in graph_audit.items() if key != "latency_ms"
    } == {
        "mode": "shadow",
        "projection_version": "sql_graph_v1",
        "seed_count": 1,
        "expanded_node_count": 1,
        "expanded_edge_count": 0,
        "path_count": 0,
        "candidate_path_count": 0,
        "emitted_path_count": 0,
        "dropped_path_count": 0,
        "dropped_path_reasons": {},
        "supporting_paper_ids": [],
        "supporting_evidence_ids": [],
        "truncated": False,
        "truncation_reason": None,
        "fallback": True,
        "fallback_reason": "insufficient_evidence",
        "paths": [],
    }
    assert graph_audit["latency_ms"] >= 0
    assert len(assistant["citations"]) == 1
    citation = assistant["citations"][0]
    assert citation["paper_title"] == "Interpretable Graph Models"
    assert "\x00" not in citation["excerpt"]
    assert citation["start_char"] == 6
    assert "[E1]" in fake_gateway.calls[-1][0]["content"]
    assert "\x00" not in fake_gateway.calls[-1][0]["content"]

    context = client.get(
        f"/api/v1/chat/conversations/{body['conversation']['id']}"
        f"/messages/{assistant['id']}/evidence/{citation['id']}/context"
    )
    assert context.status_code == 200
    assert context.json()["available"] is True
    assert context.json()["content"] == source_text


def test_workspace_chat_graph_shadow_is_audit_only_and_keeps_dense_context(
    client, db_session, fake_gateway, monkeypatch
):
    from app.domains.knowledge.models import (
        CanonicalEntity,
        EvidenceSpan,
        KnowledgeItem,
        PaperMention,
    )

    workspace = client.post("/api/v1/workspaces", json={"name": "Graph shadow"}).json()
    paper_one = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "Seed evidence", "authors": [], "year": 2024},
    ).json()
    paper_two = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "Related evidence", "authors": [], "year": 2023},
    ).json()
    entity_id = str(uuid4())
    item_one_id = str(uuid4())
    item_two_id = str(uuid4())
    evidence_id = str(uuid4())
    db_session.add(CanonicalEntity(
        id=entity_id,
        workspace_id=workspace["id"],
        type="method",
        canonical_name="Shared Method",
        normalization_key="shared method",
        status="human_confirmed",
    ))
    db_session.add_all([
        KnowledgeItem(
            id=item_one_id,
            workspace_id=workspace["id"],
            paper_id=paper_one["id"],
            canonical_entity_id=entity_id,
            type="method",
            canonical_name="Shared Method",
            content={"description": "seed"},
            source_provenance={},
            status="human_confirmed",
        ),
        KnowledgeItem(
            id=item_two_id,
            workspace_id=workspace["id"],
            paper_id=paper_two["id"],
            canonical_entity_id=entity_id,
            type="method",
            canonical_name="Shared Method",
            content={"description": "related"},
            source_provenance={},
            status="extracted_candidate",
        ),
        EvidenceSpan(
            id=evidence_id,
            workspace_id=workspace["id"],
            knowledge_item_id=item_one_id,
            paper_id=paper_one["id"],
            text="The seed paper evaluates Shared Method.",
            relation="supports",
            confidence=0.9,
            is_deleted=False,
        ),
        PaperMention(
            id=str(uuid4()),
            workspace_id=workspace["id"],
            paper_id=paper_one["id"],
            canonical_entity_id=entity_id,
            knowledge_item_id=item_one_id,
            mention_text="Shared Method",
            status="human_confirmed",
        ),
        PaperMention(
            id=str(uuid4()),
            workspace_id=workspace["id"],
            paper_id=paper_two["id"],
            canonical_entity_id=entity_id,
            knowledge_item_id=item_two_id,
            mention_text="Shared Method",
            status="extracted_candidate",
        ),
    ])
    db_session.commit()

    from app.domains.artifact.service import ArtifactService

    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace["id"],
        filename="seed.txt",
        content=b"The seed paper evaluates Shared Method.",
        mime_type="text/plain",
        kind="parsed_text",
    )
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            request_id="dense-shadow-001",
            items=[RetrievalResultItem(
                paper_id=paper_one["id"],
                chunk_id="seed-chunk",
                artifact_id=artifact.id,
                text="The seed paper evaluates Shared Method.",
                score=0.92,
            )],
            total=1,
            filters_applied={"recall_count": 1, "reranker_enabled": True},
        ),
    )
    monkeypatch.setattr(
        "app.domains.chat.service.find_chunk_record",
        lambda *_, **__: SimpleNamespace(
            source_artifact_id=artifact.id, start_char=0, end_char=42
        ),
    )
    fake_gateway.content = "只使用 dense 论文证据回答。[E1]"

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={
            "content": "哪些论文提到了这个方法？",
            "workspace_id": workspace["id"],
            "retrieval_mode": "hybrid",
        },
    )

    assert response.status_code == 200
    assistant = response.json()["assistant_message"]
    graph = assistant["retrieval_audit"]["graph"]
    assert graph["mode"] == "shadow"
    assert graph["fallback"] is False
    assert graph["path_count"] >= 1
    assert graph["candidate_path_count"] == graph["emitted_path_count"]
    assert graph["dropped_path_count"] == 0
    assert graph["dropped_path_reasons"] == {}
# Query-mode 路径必须有 source span；没有问题相关证据的相关论文不会作为诊断结果展示。
    assert paper_two["id"] not in graph["supporting_paper_ids"]
    assert all(path["evidence"] for path in graph["paths"])
    assert evidence_id in graph["supporting_evidence_ids"]
# Phase 1 仅为 shadow：GraphRAG 不会将相关论文证据加入 prompt 或回答 citations。
    assert [citation["paper_id"] for citation in assistant["citations"]] == [paper_one["id"]]
    assert "Related evidence" not in fake_gateway.calls[-1][0]["content"]


def test_workspace_chat_graph_failure_records_dense_fallback(monkeypatch, db_session):
    def fail_graph(*args, **kwargs):
        raise RuntimeError("graph unavailable")

    monkeypatch.setattr("app.domains.chat.service.build_bounded_projection", fail_graph)
    audit = ChatService(db_session)._graph_shadow_audit(
        "workspace-fallback",
        [RetrievalResultItem(paper_id="paper-dense", chunk_id="dense-chunk")],
        "dense-fallback-001",
        retrieval_mode="hybrid",
        graph_expand=None,
        graph_max_hops=None,
        graph_node_limit=None,
        graph_edge_limit=None,
    )

    assert audit["mode"] == "shadow"
    assert audit["fallback"] is True
    assert audit["fallback_reason"] == "graph_query_failed"


def test_workspace_chat_graph_timeout_keeps_completed_paths_for_diagnostics(monkeypatch, db_session):
    path = GraphRAGPathRead(
        path_id="path:timeout",
        workspace_id="workspace-timeout",
    )
    projection = BoundedGraphProjection(
        projection_version="sql_graph_v1",
        seeds=[],
        paths=[path],
        nodes=[],
        edges=[],
        evidence=[],
        supporting_paper_ids=[],
        supporting_evidence_ids=[],
    )
    monkeypatch.setattr(
        "app.domains.chat.service.build_bounded_projection",
        lambda *args, **kwargs: projection,
    )
    monkeypatch.setattr(settings, "chat_graphrag_timeout_ms", 0)

    audit = ChatService(db_session)._graph_shadow_audit(
        "workspace-timeout",
        [RetrievalResultItem(paper_id="paper-dense", chunk_id="dense-chunk")],
        "dense-timeout-001",
        retrieval_mode="hybrid",
        graph_expand=None,
        graph_max_hops=None,
        graph_node_limit=None,
        graph_edge_limit=None,
    )

    assert audit["fallback"] is True
    assert audit["fallback_reason"] == "graph_timeout"
    assert [item["path_id"] for item in audit["paths"]] == ["path:timeout"]


def test_workspace_chat_without_hits_does_not_ask_llm(client, fake_gateway, monkeypatch):
    workspace = client.post("/api/v1/workspaces", json={"name": "空工作区"}).json()
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            items=[],
            total=0,
        ),
    )

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={"content": "总结工作区论文", "workspace_id": workspace["id"]},
    )

    assert response.status_code == 200
    assistant = response.json()["assistant_message"]
    assert assistant["grounding_status"] == "no_evidence"
    assert assistant["citations"] == []
    assert "没有检索到" in assistant["content"]
    assert fake_gateway.calls == []


def test_workspace_chat_keeps_reranker_degraded_as_a_diagnostic_state(
    client,
    db_session,
    fake_gateway,
    monkeypatch,
):
    workspace = client.post("/api/v1/workspaces", json={"name": "重排降级工作区"}).json()
    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "Degraded Reranker Evidence", "authors": [], "year": 2024},
    ).json()
    from app.domains.artifact.service import ArtifactService

    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace["id"],
        filename="paper.txt",
        content=b"Evidence remains available when reranking degrades.",
        mime_type="text/plain",
        kind="parsed_text",
    )
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            status="degraded",
            diagnostic_code="reranker_degraded",
            items=[
                RetrievalResultItem(
                    paper_id=paper["id"],
                    artifact_id=artifact.id,
                    chunk_id="chunk-reranker-degraded",
                    section="Method",
                    text="Evidence remains available when reranking degrades.",
                    score=0.7,
                    retrieval_stage="candidate_recall",
                )
            ],
            total=1,
            filters_applied={
                "recall_count": 3,
                "reranker_enabled": True,
                "reranker_applied": False,
            },
        ),
    )
    monkeypatch.setattr(
        "app.domains.chat.service.find_chunk_record",
        lambda *_, **__: SimpleNamespace(source_artifact_id=artifact.id, start_char=0, end_char=52),
    )
    fake_gateway.content = "降级时仍有论文证据。[E1]"

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={"content": "重排降级时还能依据什么？", "workspace_id": workspace["id"]},
    )

    assert response.status_code == 200
    assistant = response.json()["assistant_message"]
    assert assistant["status"] == "completed"
    assert assistant["grounding_status"] == "grounded"
    assert assistant["retrieval_audit"]["status"] == "degraded"
    assert assistant["retrieval_audit"]["diagnostic_code"] == "reranker_degraded"
    assert assistant["retrieval_audit"]["reranker_status"] == "degraded"
    assert assistant["citations"]


def test_stream_workspace_chat_without_hits_has_the_same_no_evidence_contract(
    client, fake_gateway, monkeypatch
):
    workspace = client.post("/api/v1/workspaces", json={"name": "流式空工作区"}).json()
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            items=[],
            total=0,
        ),
    )
    conversation = client.post(
        "/api/v1/chat/conversations",
        json={"title": "stream no evidence", "workspace_id": workspace["id"]},
    ).json()

    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages/stream",
        json={"content": "总结工作区论文"},
    )

    assert response.status_code == 200
    assert '"type": "done"' in response.text
    detail = client.get(f"/api/v1/chat/conversations/{conversation['id']}").json()
    assistant = [item for item in detail["messages"] if item["role"] == "assistant"][-1]
    assert assistant["grounding_status"] == "no_evidence"
    assert assistant["citations"] == []
    assert "没有检索到" in assistant["content"]
    assert fake_gateway.calls == []
    assert fake_gateway.stream_calls == []


def test_sync_retrieval_failure_keeps_diagnostic_and_error_envelope(
    client, fake_gateway, monkeypatch
):
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "同步向量服务异常工作区"},
    ).json()
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            status="failed",
            error="embedding provider unavailable",
            diagnostic_code="embedding_unavailable",
        ),
    )

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={"content": "检索失败时应保持可诊断", "workspace_id": workspace["id"]},
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error"] == "workspace_retrieval_failed"
    assert detail["diagnostic_code"] == "embedding_unavailable"
    conversation_id = detail["conversation_id"]
    messages = client.get(f"/api/v1/chat/conversations/{conversation_id}").json()["messages"]
    assistant = [item for item in messages if item["role"] == "assistant"][-1]
    assert assistant["status"] == "failed"
    assert assistant["grounding_status"] == "retrieval_failed"
    assert assistant["retrieval_diagnostic_code"] == "embedding_unavailable"
    assert fake_gateway.calls == []


def test_workspace_chat_repairs_invalid_citation_once_and_persists_audit(
    client,
    db_session,
    fake_gateway,
    monkeypatch,
):
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "引用门禁", "topic": "图神经网络解释"},
    ).json()
    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "ProtGNN", "authors": [], "year": 2024},
    ).json()

    from app.domains.artifact.service import ArtifactService

    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace["id"],
        filename="paper.txt",
        content="ProtGNN uses a prototype objective.".encode("utf-8"),
        mime_type="text/plain",
        kind="parsed_text",
    )
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            items=[
                RetrievalResultItem(
                    paper_id=paper["id"],
                    artifact_id=artifact.id,
                    chunk_id="chunk-quality",
                    section="Method",
                    text="ProtGNN uses a prototype objective.",
                    score=0.9,
                    retrieval_stage="reranked",
                )
            ],
            total=1,
        ),
    )
    monkeypatch.setattr(
        "app.domains.chat.service.find_chunk_record",
        lambda *_, **__: SimpleNamespace(
            source_artifact_id=artifact.id,
            start_char=0,
            end_char=35,
        ),
    )
    fake_gateway.chat_contents = ["原回答引用不存在。[E9]", "修复后只引用现有证据。[E1]"]

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={"content": "ProtGNN 使用了什么原型思路？", "workspace_id": workspace["id"]},
    )

    assert response.status_code == 200
    assistant = response.json()["assistant_message"]
    assert assistant["content"] == "修复后只引用现有证据。[E1]"
    assert assistant["citation_quality"] == {
        "status": "repaired",
        "attempts": 1,
        "initial_broken_citations": [9],
        "initial_grounded_without_citations": False,
        "initial_broken_sources": [],
        "final_broken_citations": [],
        "final_grounded_without_citations": False,
        "final_broken_sources": [],
        "fallback": False,
    }
    assert len(fake_gateway.calls) == 2
    assert all(call["disable_thinking"] is True for call in fake_gateway.call_kwargs)
    assert all("reasoning_effort" not in call for call in fake_gateway.call_kwargs)
    assert fake_gateway.call_kwargs[1]["max_tokens"] == 2000


def test_workspace_chat_rejects_answer_when_citation_repair_still_invalid(
    client,
    db_session,
    fake_gateway,
    monkeypatch,
):
    workspace = client.post("/api/v1/workspaces", json={"name": "拒绝回答"}).json()
    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "Evidence Paper", "authors": [], "year": 2024},
    ).json()
    from app.domains.artifact.service import ArtifactService

    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace["id"],
        filename="paper.txt",
        content=b"Evidence text.",
        mime_type="text/plain",
        kind="parsed_text",
    )
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            items=[
                RetrievalResultItem(
                    paper_id=paper["id"],
                    artifact_id=artifact.id,
                    chunk_id="chunk-reject",
                    section="Results",
                    text="Evidence text.",
                    score=0.8,
                    retrieval_stage="reranked",
                )
            ],
            total=1,
        ),
    )
    monkeypatch.setattr(
        "app.domains.chat.service.find_chunk_record",
        lambda *_, **__: SimpleNamespace(source_artifact_id=artifact.id, start_char=0, end_char=14),
    )
    fake_gateway.chat_contents = ["初始回答 [E9]", "修复仍然错误 [E8]"]

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={"content": "证据支持什么？", "workspace_id": workspace["id"]},
    )

    assert response.status_code == 200
    assistant = response.json()["assistant_message"]
    assert "未能通过工作区论文引用校验" in assistant["content"]
    assert "[E8]" not in assistant["content"]
    assert assistant["citation_quality"]["status"] == "rejected"
    assert assistant["citation_quality"]["fallback"] is True
    assert assistant["citation_quality"]["final_broken_citations"] == [8]
    assert len(fake_gateway.calls) == 2


def test_workspace_chat_binds_plan_and_persists_separate_sources(
    client, db_session, fake_gateway, monkeypatch
):
    workspace = client.post("/api/v1/workspaces", json={"name": "计划问答"}).json()
    plan = _confirmed_plan(db_session, workspace["id"], "拓扑约束研究计划")
    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "ProtGNN", "authors": [], "year": 2024},
    ).json()
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            items=[
                RetrievalResultItem(
                    paper_id=paper["id"],
                    chunk_id="chunk-plan",
                    section="Method",
                    text="ProtGNN uses a contrastive objective.",
                    score=0.9,
                    retrieval_stage="reranked",
                )
            ],
            total=1,
        ),
    )
    fake_gateway.content = "计划提出的贡献见 [P1]；ProtGNN 论文证据见 [E1]。"

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={
            "content": "此研究计划相对 ProtGNN 的贡献是什么、损失函数是什么？",
            "workspace_id": workspace["id"],
            "research_plan_id": plan.id,
        },
    )

    assert response.status_code == 200, response.text
    assistant = response.json()["assistant_message"]
    assert assistant["grounding_status"] == "grounded"
    assert [source["source_type"] for source in assistant["sources"]] == ["plan", "paper"]
    assert assistant["sources"][0]["marker"] == "P1"
    assert assistant["sources"][0]["label"] == "已确认研究计划"
    assert assistant["source_check"]["ok"] is True
    prompt = fake_gateway.calls[-1][0]["content"]
    assert "独立损失函数：研究计划未提供此字段" in prompt
    assert "只有工作区论文可以使用 [E1]" in prompt


def test_ambiguous_plan_reference_requires_selection_without_llm(
    client, db_session, fake_gateway
):
    workspace = client.post("/api/v1/workspaces", json={"name": "多计划问答"}).json()
    _confirmed_plan(db_session, workspace["id"], "计划 A")
    _confirmed_plan(db_session, workspace["id"], "计划 B")

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={
            "content": "此研究计划的损失函数是什么？",
            "workspace_id": workspace["id"],
        },
    )

    assert response.status_code == 400
    assert "多个已确认研究计划" in response.json()["detail"]["message"]
    assert "计划 A" in response.json()["detail"]["message"]
    assert "计划 B" in response.json()["detail"]["message"]
    assert fake_gateway.calls == []


def test_cross_workspace_plan_is_rejected(client, db_session, fake_gateway):
    first = client.post("/api/v1/workspaces", json={"name": "当前课题"}).json()
    second = client.post("/api/v1/workspaces", json={"name": "另一个课题"}).json()
    foreign_plan = _confirmed_plan(db_session, second["id"], "跨工作区计划")

    response = client.post(
        "/api/v1/chat/conversations/send",
        json={
            "content": "请解释这个计划",
            "workspace_id": first["id"],
            "research_plan_id": foreign_plan.id,
        },
    )

    assert response.status_code == 400
    assert "属于当前工作区" in response.json()["detail"]["message"]
    assert fake_gateway.calls == []


def test_context_options_and_optional_report_code_sources_are_scoped(
    client, db_session, fake_gateway, monkeypatch
):
    from app.domains.agent.models import AgentArtifact, AgentRun

    workspace = client.post("/api/v1/workspaces", json={"name": "补充来源"}).json()
    plan = _confirmed_plan(db_session, workspace["id"], "来源边界计划")
    report_run = AgentRun(
        workspace_id=workspace["id"],
        agent_type="deep_research",
        status="succeeded",
        result={"research_plan_id": plan.id},
        input_payload={"research_plan_id": plan.id},
    )
    code_run = AgentRun(
        workspace_id=workspace["id"],
        agent_type="code_generation",
        status="succeeded",
        result={"research_plan_id": plan.id},
        input_payload={"research_plan_id": plan.id},
    )
    db_session.add_all([report_run, code_run])
    db_session.flush()
    report = AgentArtifact(
        run_id=report_run.id,
        artifact_type="deep_research_report",
        filename="report.md",
        mime_type="text/markdown",
        content="报告内说明 [E1] 只是报告自己的引用。",
        validation_status="confirmed",
    )
    code = AgentArtifact(
        run_id=code_run.id,
        artifact_type="code",
        filename="train.py",
        mime_type="text/x-python",
        content="print('candidate')",
        validation_status="not_run",
    )
    db_session.add_all([report, code])
    db_session.commit()

    options = client.get("/api/v1/chat/context-options", params={"workspace_id": workspace["id"]})
    assert options.status_code == 200, options.text
    option_body = options.json()
    assert {item["id"] for item in option_body["artifacts"]} == {report.id, code.id}
    assert {item["source_type"] for item in option_body["artifacts"]} == {"report", "code_draft"}

    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "论文证据", "authors": [], "year": 2024},
    ).json()
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            items=[RetrievalResultItem(paper_id=paper["id"], text="论文事实", score=0.8)],
            total=1,
        ),
    )
    fake_gateway.content = "计划 [P1]、论文 [E1]、报告 [D1]、代码 [C1]。"
    response = client.post(
        "/api/v1/chat/conversations/send",
        json={
            "content": "结合计划和补充材料说明贡献",
            "workspace_id": workspace["id"],
            "research_plan_id": plan.id,
            "source_artifact_ids": [report.id, code.id],
        },
    )
    assert response.status_code == 200, response.text
    assistant = response.json()["assistant_message"]
    assert [item["source_type"] for item in assistant["sources"]] == [
        "plan",
        "paper",
        "report",
        "code_draft",
    ]
    assert assistant["source_check"]["ok"] is True
    prompt = fake_gateway.calls[-1][0]["content"]
    assert "报告内说明 [来源内部标记]" in prompt
    assert "代码草案，未运行验证" in prompt


def test_conversation_workspace_is_immutable(client):
    first = client.post("/api/v1/workspaces", json={"name": "课题 A"}).json()
    second = client.post("/api/v1/workspaces", json={"name": "课题 B"}).json()
    conversation = client.post(
        "/api/v1/chat/conversations",
        json={"workspace_id": first["id"]},
    ).json()

    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages",
        json={"content": "切换课题", "workspace_id": second["id"]},
    )
    assert response.status_code == 409

    missing = client.post(
        "/api/v1/chat/conversations",
        json={"workspace_id": str(uuid4())},
    )
    assert missing.status_code == 404


def test_stream_message_emits_sse_events(client, fake_gateway):
    fake_gateway.stream_deltas = ["第一", "段", "内容"]
    conversation = client.post("/api/v1/chat/conversations", json={"title": "stream"}).json()
    resp = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages/stream",
        json={"content": "hi"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert '"type": "start"' in body
    assert '"type": "token"' in body
    assert '"content": "第一"' in body
    assert '"content": "内容"' in body
    assert '"type": "done"' in body
# 持久化的 assistant 消息是完整的
    detail = client.get(f"/api/v1/chat/conversations/{conversation['id']}").json()
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
    assert assistant["content"] == "第一段内容"
    assert assistant["status"] == "completed"
    assert assistant["prompt_chars"] == len("hi")
    assert assistant["response_chars"] == len("第一段内容")
    assert assistant["first_token_latency_ms"] >= 0
    assert assistant["completion_latency_ms"] >= assistant["first_token_latency_ms"]
    assert fake_gateway.stream_call_kwargs[-1]["disable_thinking"] is True


def test_stream_message_repairs_invalid_citation_before_persisting(
    client,
    db_session,
    fake_gateway,
    monkeypatch,
):
    workspace = client.post("/api/v1/workspaces", json={"name": "流式引用门禁"}).json()
    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "Stream Evidence", "authors": [], "year": 2024},
    ).json()
    from app.domains.artifact.service import ArtifactService

    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace["id"],
        filename="paper.txt",
        content=b"Stream evidence.",
        mime_type="text/plain",
        kind="parsed_text",
    )
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            items=[
                RetrievalResultItem(
                    paper_id=paper["id"],
                    artifact_id=artifact.id,
                    chunk_id="chunk-stream-quality",
                    section="Method",
                    text="Stream evidence.",
                    score=0.9,
                    retrieval_stage="reranked",
                )
            ],
            total=1,
        ),
    )
    monkeypatch.setattr(
        "app.domains.chat.service.find_chunk_record",
        lambda *_, **__: SimpleNamespace(source_artifact_id=artifact.id, start_char=0, end_char=16),
    )
    fake_gateway.stream_deltas = ["流式回答 [E9]"]
    fake_gateway.chat_contents = ["流式修复回答 [E1]"]
    conversation = client.post(
        "/api/v1/chat/conversations",
        json={"title": "stream quality", "workspace_id": workspace["id"]},
    ).json()

    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages/stream",
        json={"content": "证据是什么？"},
    )

    assert response.status_code == 200
    detail = client.get(f"/api/v1/chat/conversations/{conversation['id']}").json()
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
    assert assistant["content"] == "流式修复回答 [E1]"
    assert assistant["citation_quality"]["status"] == "repaired"
    assert fake_gateway.stream_call_kwargs[-1]["disable_thinking"] is True
    assert fake_gateway.call_kwargs[-1]["disable_thinking"] is True
    assert "reasoning_effort" not in fake_gateway.stream_call_kwargs[-1]
    assert "reasoning_effort" not in fake_gateway.call_kwargs[-1]


def test_stream_rejects_invalid_citation_after_one_failed_repair(
    client,
    db_session,
    fake_gateway,
    monkeypatch,
):
    workspace = client.post("/api/v1/workspaces", json={"name": "流式引用回退"}).json()
    paper = client.post(
        f"/api/v1/workspaces/{workspace['id']}/papers",
        json={"title": "Stream Fallback Evidence", "authors": [], "year": 2024},
    ).json()
    from app.domains.artifact.service import ArtifactService

    artifact = ArtifactService(db_session).save_upload(
        workspace_id=workspace["id"],
        filename="paper.txt",
        content=b"Stream fallback evidence.",
        mime_type="text/plain",
        kind="parsed_text",
    )
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            items=[
                RetrievalResultItem(
                    paper_id=paper["id"],
                    artifact_id=artifact.id,
                    chunk_id="chunk-stream-fallback",
                    section="Results",
                    text="Stream fallback evidence.",
                    score=0.8,
                    retrieval_stage="reranked",
                )
            ],
            total=1,
        ),
    )
    monkeypatch.setattr(
        "app.domains.chat.service.find_chunk_record",
        lambda *_, **__: SimpleNamespace(source_artifact_id=artifact.id, start_char=0, end_char=25),
    )
    fake_gateway.stream_deltas = ["初始回答 [E9]"]
    fake_gateway.chat_contents = ["修复仍然错误 [E8]"]
    conversation = client.post(
        "/api/v1/chat/conversations",
        json={"title": "stream fallback", "workspace_id": workspace["id"]},
    ).json()

    response = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages/stream",
        json={"content": "证据是否足够？"},
    )

    assert response.status_code == 200
    detail = client.get(f"/api/v1/chat/conversations/{conversation['id']}").json()
    assistant = [item for item in detail["messages"] if item["role"] == "assistant"][-1]
    assert "未能通过工作区论文引用校验" in assistant["content"]
    assert "[E8]" not in assistant["content"]
    assert assistant["citation_quality"]["status"] == "rejected"
    assert assistant["citation_quality"]["attempts"] == 1
    assert assistant["citation_quality"]["fallback"] is True
    assert len(fake_gateway.stream_calls) == 1
    assert len(fake_gateway.call_kwargs) == 1
    assert fake_gateway.call_kwargs[0]["disable_thinking"] is True
    assert "reasoning_effort" not in fake_gateway.call_kwargs[0]


def test_stream_retrieval_failure_emits_sse_error_and_marks_failed(
    client,
    monkeypatch,
):
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "向量服务异常工作区"},
    ).json()
    conversation = client.post(
        "/api/v1/chat/conversations",
        json={"title": "retrieval failure", "workspace_id": workspace["id"]},
    ).json()
    monkeypatch.setattr(
        "app.domains.chat.service.semantic_search",
        lambda **kwargs: RetrievalResponse(
            workspace_id=kwargs["workspace_id"],
            query=kwargs["query"],
            status="failed",
            error="embedding provider unavailable",
            diagnostic_code="embedding_unavailable",
        ),
    )

    resp = client.post(
        f"/api/v1/chat/conversations/{conversation['id']}/messages/stream",
        json={"content": "检索失败时应可恢复"},
    )

    assert resp.status_code == 200, resp.text
    assert '"type": "error"' in resp.text
    assert "无法生成查询向量" in resp.text
    assert '"diagnostic_code": "embedding_unavailable"' in resp.text
    detail = client.get(f"/api/v1/chat/conversations/{conversation['id']}").json()
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"][-1]
    assert assistant["status"] == "failed"
    assert assistant["grounding_status"] == "retrieval_failed"
    assert assistant["retrieval_diagnostic_code"] == "embedding_unavailable"


def test_stream_client_disconnect_marks_failed_not_generating(db_session, fake_gateway):
    """P0.5-1：在流式响应中途关闭 SSE 生成器（客户端断开）时，
    不能让 assistant 行永久卡在 “generating” 状态。"""
    from app.domains.chat.models import ChatMessage
    from app.domains.chat.service import ChatService

    service = ChatService(db_session, gateway=fake_gateway)
    events = service.stream_send_new("解释 GNN")
    for event in events:
        if event.get("type") == "token":
            break
    events.close()  # simulate the browser dropping the connection

    stuck = db_session.query(ChatMessage).filter_by(role="assistant", status="generating").all()
    assert stuck == []
    failed = db_session.query(ChatMessage).filter_by(role="assistant", status="failed").all()
    assert len(failed) == 1
    assert "中断" in failed[0].error_message


def test_stale_generating_row_is_healed_instead_of_bricking(db_session, fake_gateway):
    """P0.5-1：超过 STALE_GENERATING_SECONDS 未更新的 “generating” 行，
    下一次发送时应标记为失败，而不是抛出永久冲突。"""
    from datetime import datetime, timedelta, timezone

    from app.domains.chat.models import ChatMessage
    from app.domains.chat.service import STALE_GENERATING_SECONDS, ChatService

    service = ChatService(db_session, gateway=fake_gateway)
    conversation = service.create_conversation("stale", None)

    def insert_generating(updated_at):
        message = ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content="",
            status="generating",
            sequence=2,
        )
        db_session.add(message)
        db_session.commit()
# updated_at 由 onupdate 设置；显式强制设置过期时间戳。
        db_session.query(ChatMessage).filter(ChatMessage.id == message.id).update(
            {"updated_at": updated_at}
        )
        db_session.commit()
        return message

    fresh = insert_generating(datetime.now(timezone.utc))
    with pytest.raises(Exception) as conflict:
        list(service.stream_send(conversation.id, "再问一次"))  # generators run on iteration
    assert "already being generated" in str(conflict.value)
    db_session.delete(fresh)
    db_session.commit()

    insert_generating(
        datetime.now(timezone.utc) - timedelta(seconds=STALE_GENERATING_SECONDS + 60)
    )
    events = list(service.stream_send(conversation.id, "再问一次"))
    assert events[-1]["type"] == "done"
    statuses = [
        m.status
        for m in db_session.query(ChatMessage)
        .filter_by(conversation_id=conversation.id, role="assistant")
        .order_by(ChatMessage.sequence)
        .all()
    ]
    assert "generating" not in statuses
    assert statuses.count("failed") == 1  # the healed stale row
    assert statuses.count("completed") == 1  # the new answer


def test_chat_conversations_are_scoped_to_owner(client, fake_gateway):
    created = client.post(
        "/api/v1/chat/conversations",
        headers={"X-User-ID": "alice"},
        json={"title": "Alice private conversation"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]

    own = client.get(
        f"/api/v1/chat/conversations/{conversation_id}",
        headers={"X-User-ID": "alice"},
    )
    assert own.status_code == 200

    other_list = client.get(
        "/api/v1/chat/conversations",
        headers={"X-User-ID": "bob"},
    )
    assert other_list.status_code == 200
    assert other_list.json()["total"] == 0

    other_detail = client.get(
        f"/api/v1/chat/conversations/{conversation_id}",
        headers={"X-User-ID": "bob"},
    )
    assert other_detail.status_code == 404
