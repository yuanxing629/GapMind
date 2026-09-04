"""W7 Agent 全生命周期测试：Analyze / Write / Respond。

这些测试复用受控的 AgentRun/AgentStep/AgentArtifact protocol。测试最终以 "succeeded" 结束，
并在 agent_artifacts 中保存 markdown artifact（不会自动提升）。绑定 plan 时可以使用
workspace 证据；independent mode 只使用用户提供的材料，绝不伪造 workspace 引用。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.orm import Session

from app.domains.agent.service import AgentService
from app.domains.discover.models import ResearchPlan
from app.domains.retrieval.schemas import RetrievalResponse, RetrievalResultItem


@dataclass
class FakeResponse:
    content: str
    model: str = "fake-remote"
    prompt_tokens: int = 20
    completion_tokens: int = 30
    total_tokens: int = 50


class FakeGateway:
    api_key = "test-key"

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def chat_completion(self, messages, **kwargs):
        return FakeResponse(json.dumps(self.payload, ensure_ascii=False))


def _retrieval(workspace_id: str) -> RetrievalResponse:
    return RetrievalResponse(
        workspace_id=workspace_id,
        status="succeeded",
        items=[
            RetrievalResultItem(
                paper_id="paper-1",
                paper_title="Grounded Paper",
                chunk_id="chunk-1",
                section="Methods",
                text="The method uses topology-aware contrastive learning.",
                score=0.91,
            )
        ],
    )


def _workspace_plan(client, db_session: Session) -> tuple[dict, dict, ResearchPlan]:
    workspace = client.post("/api/v1/workspaces", json={"name": "Lifecycle WS"}).json()
    conversation = client.post(
        "/api/v1/chat/conversations",
        json={"title": "Lifecycle 对话", "workspace_id": workspace["id"]},
    ).json()
    plan = ResearchPlan(
        id=str(uuid4()),
        workspace_id=workspace["id"],
        status="draft",
        research_question="拓扑感知对比学习能否提升分布偏移下的鲁棒性？",
        hypothesis="拓扑正则能够提高 OOD 准确率。",
        scope_and_assumptions="节点分类",
        datasets=["Cora"],
        baselines=["GCN"],
        metrics=["OOD accuracy"],
        validation_steps=["构造分布偏移"],
        expected_supporting_result="准确率提升",
        falsification_criteria="提升小于 1%",
        risks=["数据规模有限"],
        resource_constraints="单张 GPU",
    )
    db_session.add(plan)
    db_session.commit()
    return workspace, conversation, plan


def _independent_conversation(client) -> tuple[dict, dict]:
    workspace = client.get("/api/v1/workspaces/independent").json()
    conversation = client.post(
        "/api/v1/chat/conversations",
        json={"title": "独立模式对话", "workspace_id": workspace["id"]},
    ).json()
    return workspace, conversation


def _start(client, workspace: dict, conversation: dict, *, agent_type: str, input_payload: dict):
    with patch("app.domains.agent.router.spawn_agent_task", return_value="celery-agent"):
        response = client.post(
            f"/api/v1/workspaces/{workspace['id']}/agent-runs",
            json={
                "agent_type": agent_type,
                "prompt": "执行",
                "conversation_id": conversation["id"],
                "input": input_payload,
            },
        )
    return response


def _execute(client, db_session: Session, run_id: str, payload: dict, workspace_id: str):
    import app.domains.agent.service as agent_service_module

    with patch.object(
        agent_service_module,
        "semantic_search",
        lambda **_: _retrieval(workspace_id),
    ):
        AgentService(db_session, gateway=FakeGateway(payload)).execute(run_id)
    detail = client.get(f"/api/v1/workspaces/{workspace_id}/agent-runs/{run_id}")
    assert detail.status_code == 200
    return detail.json()


def test_analyze_agent_produces_evidence_linked_memo(client, db_session: Session, monkeypatch):
    workspace, conversation, plan = _workspace_plan(client, db_session)
    started = _start(
        client, workspace, conversation,
        agent_type="analyze",
        input_payload={
            "research_plan_id": plan.id,
            "results": {"ood_accuracy": 0.82, "baseline_ood_accuracy": 0.79},
        },
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]

    body = _execute(
        client, db_session, run_id,
        {
            "verdict": "部分支持",
            "conclusion": "OOD 准确率提升 3%，支持假设但对大偏移失效。",
            "key_findings": ["拓扑正则带来有限提升"],
            "evidence_refs": ["E1"],
            "risks": ["数据规模有限"],
        },
        workspace["id"],
    )

    assert body["status"] == "succeeded"
    assert body["result"]["verdict"] == "部分支持"
    assert body["result"]["research_plan_id"] == plan.id
    artifact = body["artifacts"][0]
    assert artifact["artifact_type"] == "analysis"
    assert artifact["filename"] == "research_memo.md"
    assert "[E1]" in artifact["content"] or "E1" in artifact["content"]


def test_write_agent_produces_paper_draft(client, db_session: Session):
    workspace, conversation, plan = _workspace_plan(client, db_session)
    started = _start(
        client, workspace, conversation,
        agent_type="write",
        input_payload={"research_plan_id": plan.id},
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]

    body = _execute(
        client, db_session, run_id,
        {
            "title": "Robustness of Topology-Aware GNNs under Distribution Shift",
            "abstract": "我们研究了拓扑感知对比学习在分布偏移下的鲁棒性 [E1]。",
            "introduction": "背景与动机。",
            "method": "方法。",
            "experiments": "实验。",
            "conclusion": "结论。",
            "evidence_refs": ["E1"],
        },
        workspace["id"],
    )

    assert body["status"] == "succeeded"
    assert body["result"]["title"].startswith("Robustness")
    artifact = body["artifacts"][0]
    assert artifact["artifact_type"] == "paper_draft"
    assert artifact["filename"] == "paper_draft.md"
    assert "## Abstract" in artifact["content"]


def test_respond_agent_produces_rebuttal(client, db_session: Session):
    workspace, conversation, plan = _workspace_plan(client, db_session)
    started = _start(
        client, workspace, conversation,
        agent_type="respond",
        input_payload={
            "research_plan_id": plan.id,
            "reviewer_comments": "1) 缺乏与强基线的对比；2) 数据集规模太小。",
        },
    )
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]

    body = _execute(
        client, db_session, run_id,
        {
            "summary": "已逐条回应审稿意见。",
            "responses": [
                {
                    "comment": "缺乏与强基线的对比",
                    "response": "我们在实验中补充了 GCN 与拓扑基线对比 [E1]。",
                    "evidence_refs": ["E1"],
                },
                {"comment": "数据集规模太小", "response": "后续将扩展数据集。", "evidence_refs": []},
            ],
            "evidence_refs": ["E1"],
        },
        workspace["id"],
    )

    assert body["status"] == "succeeded"
    assert len(body["result"]["responses"]) == 2
    artifact = body["artifacts"][0]
    assert artifact["artifact_type"] == "rebuttal"
    assert artifact["filename"] == "rebuttal.md"
    assert "### 意见 1" in artifact["content"]


def test_analyze_standalone_without_plan(client, db_session: Session):
    """P1：analyze 在没有 plan 时于系统 independent workspace 中执行。"""
    workspace, conversation = _independent_conversation(client)
    started = _start(
        client,
        workspace,
        conversation,
        agent_type="analyze",
        input_payload={"results": {"x": 1}, "research_plan_id": ""},
    )
    assert started.status_code == 202, started.text
    body = _execute(
        client,
        db_session,
        started.json()["id"],
        {
            "verdict": "证据不足",
            "conclusion": "用户提供的结果不足以支持假设。",
            "key_findings": ["需要补充基线对照"],
            "evidence_refs": ["E1"],
            "risks": ["数据不完整"],
        },
        workspace["id"],
    )
    assert body["status"] == "succeeded"
    assert body["result"]["independent"] is True
    assert body["context_snapshot"]["evidence"] == []


def test_write_standalone_without_plan_uses_only_user_material(client, db_session: Session):
    workspace, conversation = _independent_conversation(client)
    started = _start(
        client,
        workspace,
        conversation,
        agent_type="write",
        input_payload={"research_plan_id": ""},
    )
    assert started.status_code == 202, started.text
    body = _execute(
        client,
        db_session,
        started.json()["id"],
        {
            "title": "Standalone Research Draft",
            "abstract": "基于用户提供材料的摘要。",
            "introduction": "背景。",
            "method": "方法。",
            "experiments": "实验。",
            "conclusion": "结论。",
            "evidence_refs": ["E1"],
        },
        workspace["id"],
    )
    assert body["status"] == "succeeded"
    assert body["result"]["independent"] is True
    assert body["artifacts"][0]["artifact_type"] == "paper_draft"


def test_respond_standalone_without_plan_does_not_retrieve_missing_plan(client, db_session: Session):
    workspace, conversation = _independent_conversation(client)
    started = _start(
        client,
        workspace,
        conversation,
        agent_type="respond",
        input_payload={
            "research_plan_id": "",
            "reviewer_comments": "缺少消融实验。",
        },
    )
    assert started.status_code == 202, started.text
    body = _execute(
        client,
        db_session,
        started.json()["id"],
        {
            "summary": "已给出补充实验计划。",
            "responses": [
                {
                    "comment": "缺少消融实验。",
                    "response": "我们将补充关键模块消融实验。",
                    "evidence_refs": ["E1"],
                }
            ],
            "evidence_refs": ["E1"],
        },
        workspace["id"],
    )
    assert body["status"] == "succeeded"
    assert body["result"]["independent"] is True
    assert body["artifacts"][0]["artifact_type"] == "rebuttal"


def test_independent_workspace_rejects_corpus_bound_agents(client):
    workspace, conversation = _independent_conversation(client)
    response = _start(
        client,
        workspace,
        conversation,
        agent_type="research_plan",
        input_payload={},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "agent_input_invalid"


def test_optional_plan_must_belong_to_the_current_workspace(client, db_session: Session):
    _, _, plan = _workspace_plan(client, db_session)
    other = client.post("/api/v1/workspaces", json={"name": "Other lifecycle workspace"}).json()
    conversation = client.post(
        "/api/v1/chat/conversations",
        json={"title": "Other workspace conversation", "workspace_id": other["id"]},
    ).json()
    response = _start(
        client,
        other,
        conversation,
        agent_type="write",
        input_payload={"research_plan_id": plan.id},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "agent_input_invalid"


def test_respond_requires_reviewer_comments(client, db_session: Session):
    workspace, conversation, plan = _workspace_plan(client, db_session)
    response = _start(
        client, workspace, conversation,
        agent_type="respond",
        input_payload={"research_plan_id": plan.id},  # missing reviewer_comments
    )
    assert response.status_code == 422
