"""Controlled Agent API tests; no external model, Milvus, Redis, or Docker calls."""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.domains.agent.models import AgentArtifact
from app.domains.agent.service import AgentConflictError, AgentInputError, AgentService
from app.domains.discover.models import (
    DiscoverExternalCandidate,
    DiscoverRun,
    OpportunityEvidence,
    OpportunityVersion,
    ResearchOpportunity,
    ResearchPlan,
)
from app.domains.paper.models import Paper
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

    def __init__(
        self,
        payload: dict,
        blueprint_payload: dict | None = None,
        rubric_payload: dict | None = None,
        invalid_first_file: bool = False,
        fail_paths: set[str] | None = None,
    ) -> None:
        self.payload = payload
        self.blueprint_payload = blueprint_payload
        self.rubric_payload = rubric_payload
        self.invalid_first_file = invalid_first_file
        self.fail_paths = fail_paths or set()
        self.file_calls = 0
        self.calls: list[str] = []

    def chat_completion(self, messages, **kwargs):
        user_prompt = messages[-1]["content"] if messages else ""
        self.calls.append(user_prompt)
        if self.blueprint_payload is not None and "只做设计" in user_prompt:
            payload = self.blueprint_payload
        elif self.rubric_payload is not None and "覆盖度自检" in user_prompt:
            payload = self.rubric_payload
        else:
            self.file_calls += 1
            if self.invalid_first_file and self.file_calls == 1:
                # truncated mid-string, like a max_tokens cut: no closing braces
                return FakeResponse('{"files": [{"path": "README.md", "content": "tru')
            if self.fail_paths:
                # match the exact target-file spec line, e.g. 目标文件：{"path": "README.md", ...}
                import re as _re

                match = _re.search(r'目标文件：(\{.*?\})\n', user_prompt)
                if match and '"path": "' in match.group(1):
                    import json as _json

                    try:
                        target = _json.loads(match.group(1))
                    except ValueError:
                        target = {}
                    if target.get("path") in self.fail_paths:
                        return FakeResponse("not json at all")
            payload = self.payload
        return FakeResponse(json.dumps(payload, ensure_ascii=False))


def _workspace_conversation(client):
    workspace = client.post("/api/v1/workspaces", json={"name": "Agent WS"}).json()
    conversation = client.post(
        "/api/v1/chat/conversations",
        json={"title": "Agent 对话", "workspace_id": workspace["id"]},
    ).json()
    return workspace, conversation


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
                text="The method uses topology-aware\x00 contrastive learning.",
                score=0.91,
            )
        ],
    )


def test_research_agent_persists_steps_artifact_and_confirmed_plan(
    client, db_session: Session, monkeypatch
):
    workspace, conversation = _workspace_conversation(client)
    with patch("app.domains.agent.router.spawn_agent_task", return_value="celery-agent"):
        response = client.post(
            f"/api/v1/workspaces/{workspace['id']}/agent-runs",
            json={
                "agent_type": "research_plan",
                "prompt": "设计图神经网络鲁棒性实验",
                "conversation_id": conversation["id"],
                "input": {"resource_constraints": "单张 GPU"},
            },
        )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    monkeypatch.setattr(
        "app.domains.agent.service.semantic_search", lambda **_: _retrieval(workspace["id"])
    )
    gateway = FakeGateway(
        {
            "title": "拓扑感知对比学习的分布偏移鲁棒性研究",
            "research_question": "拓扑感知对比学习能否提升分布偏移下的鲁棒性？",
            "hypothesis": "拓扑正则能够提高 OOD 准确率。",
            "scope_and_assumptions": "节点分类",
            "datasets": ["Cora"],
            "baselines": ["GCN"],
            "metrics": ["OOD accuracy"],
            "validation_steps": ["构造分布偏移", "比较基线"],
            "expected_supporting_result": "准确率提升",
            "falsification_criteria": "提升小于 1%",
            "risks": ["数据规模有限"],
            "resource_constraints": "单张 GPU",
            "evidence_refs": ["E1"],
        }
    )
    AgentService(db_session, gateway=gateway).execute(run_id)

    detail = client.get(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{run_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "waiting_for_user"
    assert len(body["steps"]) == 3
    assert body["artifacts"][0]["filename"] == "research_plan.md"

    confirmed = client.post(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{run_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    plan_id = confirmed.json()["research_plan_id"]
    plan = db_session.get(ResearchPlan, plan_id)
    assert plan is not None
    assert plan.title == "拓扑感知对比学习的分布偏移鲁棒性研究"
    assert plan.source_type == "agent"
    assert plan.opportunity_id is None


def test_deep_research_agent_binds_plan_generates_grounded_report_and_waits_for_review(
    client, db_session: Session, monkeypatch
):
    workspace, conversation = _workspace_conversation(client)
    plan = ResearchPlan(
        workspace_id=workspace["id"],
        opportunity_id=None,
        opportunity_version_id=None,
        source_type="opportunity",
        status="draft",
        title="图神经网络分布偏移鲁棒性研究",
        research_question="拓扑约束能否提升图神经网络在分布偏移下的鲁棒性？",
        hypothesis="拓扑约束能够提高 OOD 准确率。",
        scope_and_assumptions="节点分类",
        datasets=["Cora"],
        baselines=["GCN"],
        metrics=["OOD accuracy"],
        validation_steps=["构造分布偏移", "比较基线"],
        expected_supporting_result="准确率提升",
        falsification_criteria="提升小于 1%",
        risks=["数据规模有限"],
        resource_constraints="单张 GPU",
    )
    db_session.add(plan)
    db_session.commit()
    with patch("app.domains.agent.router.spawn_agent_task", return_value="celery-deep"):
        response = client.post(
            f"/api/v1/workspaces/{workspace['id']}/agent-runs",
            json={
                "agent_type": "deep_research",
                "prompt": "综合支持证据和反证，精炼实验方案",
                "conversation_id": conversation["id"],
                "input": {"research_plan_id": plan.id},
            },
        )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert response.json()["context_snapshot"]["research_plan"]["title"] == plan.title

    monkeypatch.setattr(
        "app.domains.agent.service.semantic_search", lambda **_: _retrieval(workspace["id"])
    )
    gateway = FakeGateway(
        {
            "title": "拓扑约束下的图神经网络鲁棒性深度研究",
            "executive_summary": "现有证据支持继续验证，但跨数据集外推仍不确定。",
            "research_landscape": "现有工作主要关注单一分布偏移设置。",
            "supporting_findings": ["拓扑感知机制与鲁棒性提升相关 [W1]"],
            "counter_findings": ["尚缺少跨数据集独立复现"],
            "unresolved_questions": ["提升是否依赖图同配性？"],
            "refined_hypothesis": "在中高同配图上，拓扑约束可稳定提升 OOD 准确率。",
            "recommended_methodology": ["按同配性分层实验"],
            "proposed_method": {
                "name_zh": "拓扑约束鲁棒学习框架",
                "core_idea": "联合优化分类目标和拓扑一致性目标。",
                "modules": ["图编码器", "拓扑一致性正则器"],
                "objective_function": {
                    "latex": "\\mathcal{L}=\\mathcal{L}_{task}+\\lambda\\mathcal{L}_{topo}",
                    "explanation": "最小化任务损失与拓扑一致性损失的加权和。",
                    "symbols": "lambda 为拓扑正则强度。",
                },
                "formulas": [
                    {
                        "name": "联合训练目标",
                        "latex": "\\mathcal{L}=\\mathcal{L}_{task}+\\lambda\\mathcal{L}_{topo}",
                        "explanation": "在完成节点分类的同时约束拓扑表示。",
                        "symbols": "lambda 为拓扑正则强度。",
                    },
                    {
                        "name": "鲁棒性增益",
                        "latex": "\\Delta_{ood}=Acc_{ours}^{ood}-Acc_{base}^{ood}",
                        "explanation": "衡量候选方法相对基线的分布外准确率增益。",
                        "symbols": "Acc 表示分布外准确率。",
                    },
                ],
                "algorithm_steps": ["编码图结构", "计算联合损失", "反向传播"],
                "implementation_details": ["使用 PyTorch Geometric 实现"],
            },
            "experimental_design": {
                "datasets": ["Cora", "Citeseer"],
                "baselines": ["GCN", "GraphSAGE"],
                "metrics": ["OOD accuracy", "Macro-F1", "训练时间"],
                "ablations": ["移除拓扑正则"],
                "statistical_tests": ["五个随机种子的配对 t 检验"],
                "expected_supporting_results": ["OOD accuracy 稳定提升"],
                "falsification_criteria": ["提升不显著或仅在单一数据集出现"],
            },
            "experiment_plan": ["在 Cora 上构造偏移", "进行消融"],
            "novelty_assessment": "边界条件验证具有增量新颖性。",
            "risk_register": ["证据仅来自一个工作区片段"],
            "next_actions": ["补充第二个独立数据集"],
            "evidence_refs": ["W1", "NOT_REAL"],
        }
    )
    AgentService(db_session, gateway=gateway).execute(run_id)

    detail = client.get(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{run_id}").json()
    assert detail["status"] == "waiting_for_user"
    assert [step["stage"] for step in detail["steps"]] == [
        "plan_binding",
        "evidence_collection",
        "deep_synthesis",
        "evidence_gate",
    ]
    assert detail["result"]["evidence_refs"] == ["W1"]
    assert len(detail["result"]["proposed_method"]["formulas"]) == 2
    assert detail["result"]["experimental_design"]["datasets"] == ["Cora", "Citeseer"]
    assert "数学定义与候选公式" in detail["artifacts"][0]["content"]
    assert detail["artifacts"][0]["filename"] == "deep_research_report.md"

    confirmed = client.post(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{run_id}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["research_plan_id"] == plan.id
    assert confirmed.json()["run"]["result"]["review_status"] == "confirmed"


def test_agent_evidence_resolves_workspace_paper_title(client, db_session: Session):
    workspace, _ = _workspace_conversation(client)
    db_session.add(
        Paper(
            id="paper-real",
            workspace_id=workspace["id"],
            title="真实工作区论文标题",
            authors=[],
            source="manual",
            parse_status="parsed",
            extract_status="extracted",
            is_deleted=False,
        )
    )
    db_session.commit()

    evidence = AgentService(db_session)._evidence_list(
        [
            RetrievalResultItem(
                paper_id="paper-real",
                paper_title=None,
                chunk_id="chunk-real",
                section="Methods",
                text="evidence text",
                score=0.9,
            )
        ]
    )

    assert evidence[0]["paper_title"] == "真实工作区论文标题"


def test_agent_evidence_resolves_external_candidate_title(client, db_session: Session):
    workspace, _ = _workspace_conversation(client)
    discover_run = DiscoverRun(id=str(uuid4()), workspace_id=workspace["id"])
    opportunity = ResearchOpportunity(
        id=str(uuid4()),
        workspace_id=workspace["id"],
        title="机会",
        summary="摘要",
        rationale="理由",
    )
    version = OpportunityVersion(
        id=str(uuid4()),
        opportunity_id=opportunity.id,
        version_number=1,
        title="机会版本",
        problem_statement="问题",
    )
    candidate = DiscoverExternalCandidate(
        id=str(uuid4()),
        discover_run_id=discover_run.id,
        query="q",
        rank=1,
        external_paper_id="S2-real",
        title="真实外部论文标题",
        authors=[],
        snapshot_payload={},
    )
    evidence = OpportunityEvidence(
        opportunity_version_id=version.id,
        relation="supports",
        source_scope="external",
        evidence_level="metadata_only",
        external_candidate_id=candidate.id,
        display_excerpt="external evidence",
        snapshot_payload={},
    )
    db_session.add_all([discover_run, opportunity, version, candidate, evidence])
    db_session.commit()

    result = AgentService(db_session)._discover_evidence(
        {"opportunity_version_id": version.id}
    )

    assert result[0]["paper_title"] == "真实外部论文标题"
    assert result[0]["external_candidate_id"] == candidate.id


def test_code_agent_requires_plan_and_generates_safe_downloadable_files(
    client, db_session: Session, monkeypatch
):
    workspace, conversation = _workspace_conversation(client)
    missing = client.post(
        f"/api/v1/workspaces/{workspace['id']}/agent-runs",
        json={
            "agent_type": "code_generation",
            "prompt": "生成代码",
            "conversation_id": conversation["id"],
            "input": {},
        },
    )
    assert missing.status_code == 422

    plan = ResearchPlan(
        workspace_id=workspace["id"],
        opportunity_id=None,
        opportunity_version_id=None,
        source_type="agent",
        status="draft",
        research_question="Test graph robustness",
        hypothesis="Regularization improves OOD",
        scope_and_assumptions="Node classification",
        datasets=["Cora"],
        baselines=["GCN"],
        metrics=["accuracy"],
        validation_steps=["train", "evaluate"],
        expected_supporting_result="gain",
        falsification_criteria="no gain",
        risks=[],
        resource_constraints="CPU",
    )
    db_session.add(plan)
    db_session.commit()
    with patch("app.domains.agent.router.spawn_agent_task", return_value="celery-code"):
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/agent-runs",
            json={
                "agent_type": "code_generation",
                "prompt": "生成最小实验",
                "conversation_id": conversation["id"],
                "input": {"research_plan_id": plan.id, "framework": "PyTorch"},
            },
        )
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    monkeypatch.setattr(
        "app.domains.agent.service.semantic_search", lambda **_: _retrieval(workspace["id"])
    )
    gateway = FakeGateway(
        blueprint_payload={
            "summary": "最小对比实验项目",
            "modules": [{"name": "training", "responsibility": "训练与评估入口"}],
            "files": [
                {
                    "path": "README.md",
                    "language": "markdown",
                    "purpose": "项目说明",
                    "depends_on": [],
                    "evidence_refs": [],
                },
                {
                    "path": "requirements.txt",
                    "language": "text",
                    "purpose": "依赖清单",
                    "depends_on": [],
                    "evidence_refs": [],
                },
                {
                    "path": "src/train.py",
                    "language": "python",
                    "purpose": "训练入口",
                    "depends_on": [],
                    "evidence_refs": ["E1", "E9"],
                },
                {
                    "path": "../escape.py",
                    "language": "python",
                    "purpose": "路径逃逸",
                    "depends_on": [],
                    "evidence_refs": [],
                },
            ],
            "entrypoint": "src/train.py",
            "test_files": [],
        },
        payload={
            "files": [
                {"path": "src/train.py", "language": "python", "content": "print('train')"},
            ],
        },
        rubric_payload={
            "items": [
                {"dimension": "dataset", "target": "Cora", "status": "covered", "note": "配置内置"},
                {"dimension": "baseline", "target": "GCN", "status": "covered", "note": "基线实现"},
                {"dimension": "metric", "target": "accuracy", "status": "covered", "note": "评估函数"},
                {"dimension": "validation_step", "target": "train", "status": "partial", "note": "入口存在"},
                {"dimension": "validation_step", "target": "evaluate", "status": "missing", "note": "未实现"},
            ],
            "overall_note": "骨架可用，评估流程待补",
        },
    )
    AgentService(db_session, gateway=gateway).execute(run_id)
    detail = client.get(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{run_id}").json()
    assert detail["status"] == "succeeded"
    # escape path dropped, README/requirements generated even though the file
    # payload only carries train.py (per-file calls fall back to any returned file)
    assert {item["filename"] for item in detail["artifacts"]} == {
        "README.md",
        "requirements.txt",
        "src/train.py",
        "code_rubric.md",
    }
    # evidence passport (Phase A5): refs are validated against real evidence ids
    train_artifact = next(item for item in detail["artifacts"] if item["filename"] == "src/train.py")
    assert train_artifact["metadata"]["evidence_refs"] == ["E1"]
    assert detail["result"]["blueprint"]["files"] == [
        "README.md",
        "requirements.txt",
        "src/train.py",
    ]
    assert detail["result"]["token_usage"]["llm_calls"] == 5
    steps = {step["stage"]: step for step in detail["steps"]}
    assert steps["module_design"]["summary"].startswith("蓝图：1 个模块")
    assert steps["static_review"]["sequence"] == 6
    # static review is split into delivery blockers and improvement items;
    # no test file means 3/3 blockers and 2/3 improvement checks pass.
    assert steps["static_review"]["summary"] == "交付完整性检查：阻断项 3/3，改进项 2/3"
    checks = detail["result"]["static_review"]["checks"]
    check_names = {check["name"]: check["passed"] for check in checks}
    check_severity = {check["name"]: check["severity"] for check in checks}
    assert check_severity["syntax_valid"] == "blocking"
    assert check_severity["test_present"] == "advisory"
    assert detail["result"]["static_review"]["blocking"] == {"passed": 3, "total": 3}
    assert detail["result"]["static_review"]["advisory"] == {"passed": 2, "total": 3}
    assert check_names["test_present"] is False
    assert check_names["imports_covered_by_requirements"] is True
    assert check_names["syntax_valid"] is True  # Phase B-removal: pure-AST syntax gate
    # rubric self-check (A4): counts mirror the fake payload
    assert detail["result"]["rubric"] == {"covered": 3, "partial": 1, "missing": 1}
    # known_gaps (A4 follow-up): structured partial/missing items, one per plan entry
    assert detail["result"]["known_gaps"] == [
        {"dimension": "validation_step", "target": "train", "status": "partial", "note": "入口存在"},
        {"dimension": "validation_step", "target": "evaluate", "status": "missing", "note": "未实现"},
    ]
    rubric_artifact = next(
        item for item in detail["artifacts"] if item["artifact_type"] == "code_review"
    )
    assert rubric_artifact["filename"] == "code_rubric.md"
    assert "❌ 未覆盖" in rubric_artifact["content"]
    # blueprint prompt is the only "design" call; each file is its own generation call
    design_calls = [c for c in gateway.calls if "只做设计" in c]
    assert len(design_calls) == 1
    gen_calls = [c for c in gateway.calls if "只生成指定的这一个文件" in c]
    assert len(gen_calls) == 3
    train_call = next(c for c in gen_calls if "src/train.py" in c)
    assert "E9" not in train_call  # invalid refs filtered before grounding
    bundle = client.get(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{run_id}/bundle")
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as zf:
        names = zf.namelist()
        assert "RESEARCH_PLAN.md" in names  # plan included alongside the code
        assert "ARTIFACT_STATUS.json" in names
        assert "README.md" in names
        assert "src/train.py" in names
        assert "研究问题" in zf.read("RESEARCH_PLAN.md").decode("utf-8")
        artifact_status = json.loads(zf.read("ARTIFACT_STATUS.json").decode("utf-8"))
        assert artifact_status["generated_by"] == "ai"
        statuses = {item["filename"]: item["validation_status"] for item in artifact_status["artifacts"]}
        assert statuses["src/train.py"] == "not_run"


def test_code_agent_generates_one_preview_only_repair_candidate(
    client, db_session: Session
):
    workspace, conversation = _workspace_conversation(client)
    plan = ResearchPlan(
        workspace_id=workspace["id"],
        opportunity_id=None,
        opportunity_version_id=None,
        source_type="opportunity",
        status="confirmed",
        title="候选修复测试计划",
        research_question="方法是否提升节点分类效果？",
        hypothesis="方法能够提升准确率。",
        scope_and_assumptions="固定数据集",
        datasets=["Cora"],
        baselines=["GCN"],
        metrics=["accuracy"],
        validation_steps=["train", "evaluate"],
        expected_supporting_result="准确率提升",
        falsification_criteria="没有提升",
        risks=[],
        resource_constraints="单张 GPU",
    )
    db_session.add(plan)
    db_session.commit()
    parent = AgentService(db_session).start(
        workspace["id"],
        agent_type="code_generation",
        prompt="生成实验项目",
        conversation_id=conversation["id"],
        input_payload={"research_plan_id": plan.id, "framework": "PyTorch"},
    )
    parent.status = "succeeded"
    parent.current_stage = "artifacts_ready"
    parent.result = {
        "research_plan_id": plan.id,
        "static_review": {
            "checks": [
                {"name": "test_present", "passed": False, "detail": "没有识别到测试文件"},
            ]
        },
    }
    parent.context_snapshot = {
        "blueprint": {
            "files": [
                {"path": "README.md", "purpose": "项目说明"},
                {"path": "requirements.txt", "purpose": "依赖清单"},
                {"path": "src/train.py", "purpose": "训练入口"},
            ],
            "entrypoint": "src/train.py",
        }
    }
    db_session.add(
        AgentArtifact(
            run_id=parent.id,
            artifact_type="code",
            filename="README.md",
            mime_type="text/markdown",
            content="# 实验项目",
            metadata_payload={"language": "markdown"},
            validation_status="not_run",
        )
    )
    db_session.add(
        AgentArtifact(
            run_id=parent.id,
            artifact_type="code",
            filename="requirements.txt",
            mime_type="text/plain",
            content="",
            metadata_payload={"language": "text"},
            validation_status="not_run",
        )
    )
    db_session.add(
        AgentArtifact(
            run_id=parent.id,
            artifact_type="code",
            filename="src/train.py",
            mime_type="text/x-python",
            content="def train():\n    return 1\n",
            metadata_payload={"language": "python"},
            validation_status="not_run",
        )
    )
    db_session.commit()

    gateway = FakeGateway(
        {"files": [{"path": "tests/test_train.py", "language": "python", "content": "def test_train():\n    assert True\n"}]}
    )
    service = AgentService(db_session, gateway=gateway)
    child = service.start(
        workspace["id"],
        agent_type="code_generation",
        prompt="修复交付完整性缺口",
        conversation_id=conversation["id"],
        input_payload={"research_plan_id": plan.id, "repair_parent_run_id": parent.id},
    )
    service.execute(child.id)
    detail = client.get(
        f"/api/v1/workspaces/{workspace['id']}/agent-runs/{child.id}"
    ).json()
    assert detail["parent_run_id"] == parent.id
    assert detail["result"]["candidate_repair"]["attempt"] == 1
    assert detail["result"]["candidate_repair"]["changed_files"] == ["tests/test_train.py"]
    assert detail["result"]["validation"]["status"] == "not_run"
    assert {item["filename"] for item in detail["artifacts"]} == {
        "tests/test_train.py",
        "code_repair_review.md",
        "code_repair_diff.md",
    }
    candidate_artifact = next(
        item for item in detail["artifacts"] if item["artifact_type"] == "code"
    )
    assert candidate_artifact["metadata"]["candidate_repair"] is True
    assert detail["result"]["candidate_repair"]["before"] == {
        "blocking": {"passed": 0, "total": 0},
        "advisory": {"passed": 0, "total": 1},
    }
    assert detail["result"]["candidate_repair"]["after"] == {
        "blocking": {"passed": 3, "total": 3},
        "advisory": {"passed": 3, "total": 3},
    }
    assert len([call for call in gateway.calls if "候选修订" in call]) == 1

    other_workspace, other_conversation = _workspace_conversation(client)
    with pytest.raises(AgentInputError):
        service.start(
            other_workspace["id"],
            agent_type="code_generation",
            prompt="跨工作区修复",
            conversation_id=other_conversation["id"],
            input_payload={"research_plan_id": plan.id, "repair_parent_run_id": parent.id},
        )

    with pytest.raises(AgentConflictError):
        service.start(
            workspace["id"],
            agent_type="code_generation",
            prompt="再次修复",
            conversation_id=conversation["id"],
            input_payload={"research_plan_id": plan.id, "repair_parent_run_id": parent.id},
        )


def test_code_agent_recovers_from_truncated_file_json(
    client, db_session: Session, monkeypatch
):
    workspace, conversation = _workspace_conversation(client)
    plan = ResearchPlan(
        workspace_id=workspace["id"],
        opportunity_id=None,
        opportunity_version_id=None,
        source_type="agent",
        status="draft",
        research_question="Q",
        hypothesis="H",
        scope_and_assumptions="",
        datasets=[],
        baselines=[],
        metrics=[],
        validation_steps=[],
        expected_supporting_result="",
        falsification_criteria="",
        risks=[],
        resource_constraints="",
    )
    db_session.add(plan)
    db_session.commit()
    with patch("app.domains.agent.router.spawn_agent_task", return_value="celery-code"):
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/agent-runs",
            json={
                "agent_type": "code_generation",
                "prompt": "生成最小实验",
                "conversation_id": conversation["id"],
                "input": {"research_plan_id": plan.id},
            },
        )
    run_id = created.json()["id"]
    monkeypatch.setattr(
        "app.domains.agent.service.semantic_search", lambda **_: _retrieval(workspace["id"])
    )
    gateway = FakeGateway(
        blueprint_payload={
            "summary": "最小项目",
            "modules": [],
            "files": [
                {
                    "path": "README.md",
                    "language": "markdown",
                    "purpose": "说明",
                    "depends_on": [],
                    "evidence_refs": [],
                }
            ],
        },
        payload={
            "files": [
                {"path": "README.md", "language": "markdown", "content": "# ok"},
            ],
        },
        invalid_first_file=True,
    )
    AgentService(db_session, gateway=gateway).execute(run_id)
    detail = client.get(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{run_id}").json()
    assert detail["status"] == "succeeded"
    assert sorted(item["filename"] for item in detail["artifacts"] if item["artifact_type"] == "code") == [
        "README.md", "requirements.txt"
    ]
    # the retry carries the brevity directive instead of resending verbatim
    assert any("大幅精简" in c for c in gateway.calls)
    # blueprint + 2 README attempts + requirements + rubric
    assert detail["result"]["token_usage"]["llm_calls"] == 5


def test_code_rag_facets_label_evidence(client, db_session: Session, monkeypatch):
    workspace, conversation = _workspace_conversation(client)
    plan = ResearchPlan(
        workspace_id=workspace["id"],
        opportunity_id=None,
        opportunity_version_id=None,
        source_type="agent",
        status="draft",
        research_question="Contrastive graph learning",
        hypothesis="Topology-aware contrast helps",
        scope_and_assumptions="",
        datasets=["Cora"],
        baselines=["GCN"],
        metrics=["accuracy"],
        validation_steps=["train"],
        expected_supporting_result="",
        falsification_criteria="",
        risks=[],
        resource_constraints="",
    )
    db_session.add(plan)
    db_session.commit()

    def routed(workspace_id=None, query="", top_k=None, use_reranker=None):
        def item(chunk_id, text, score, section="Methods"):
            return RetrievalResultItem(
                paper_id="paper-1",
                paper_title="Grounded Paper",
                chunk_id=chunk_id,
                section=section,
                text=text,
                score=score,
            )
        if "方法步骤" in query:
            items = [item("chunk-method", "topology-aware contrastive learning algorithm", 0.9)]
        elif "公式" in query:
            items = [item("chunk-formula", "L = -sum(log p)", 0.85, "Equations")]
        elif "实验设置" in query:
            items = [item("chunk-setup", "Adam lr=1e-3 batch 128", 0.8)]
        elif "数据预处理" in query:
            items = [item("chunk-pre", "normalize features split 8:2", 0.75)]
        else:
            items = [item("chunk-1", "base contrastive learning", 0.7)]
        return RetrievalResponse(workspace_id=workspace_id, status="succeeded", items=items)

    monkeypatch.setattr("app.domains.agent.service.semantic_search", routed)
    with patch("app.domains.agent.router.spawn_agent_task", return_value="celery-code"):
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/agent-runs",
            json={
                "agent_type": "code_generation",
                "prompt": "生成最小实验",
                "conversation_id": conversation["id"],
                "input": {"research_plan_id": plan.id},
            },
        )
    run_id = created.json()["id"]
    gateway = FakeGateway(
        blueprint_payload={
            "summary": "最小项目",
            "modules": [],
            "files": [
                {
                    "path": "src/train.py",
                    "language": "python",
                    "purpose": "训练入口",
                    "depends_on": [],
                    "evidence_refs": ["E1", "E9"],
                }
            ],
        },
        payload={
            "files": [
                {"path": "src/train.py", "language": "python", "content": "print('train')"},
            ],
        },
    )
    AgentService(db_session, gateway=gateway).execute(run_id)
    run = AgentService(db_session).get(workspace["id"], run_id)
    evidence = run.context_snapshot["evidence"]
    # facets are the primary source; the single-query fallback only kicks in when
    # every facet search comes back empty
    assert len(evidence) == 4
    assert "chunk-1" not in {entry["chunk_id"] for entry in evidence}
    by_chunk = {entry["chunk_id"]: entry for entry in evidence}
    assert "method" in by_chunk["chunk-method"]["facets"]
    assert "formula" in by_chunk["chunk-formula"]["facets"]
    assert "setup" in by_chunk["chunk-setup"]["facets"]
    assert "preprocess" in by_chunk["chunk-pre"]["facets"]
    assert all(entry["is_code_grounding"] for entry in evidence)
    # fallback branch: every facet search fails -> single-query evidence is used
    def failed(workspace_id=None, query="", top_k=None, use_reranker=None):
        if any(marker in query for marker in ("方法步骤", "公式", "实验设置", "数据预处理")):
            return RetrievalResponse(workspace_id=workspace_id, status="failed", items=[])
        return RetrievalResponse(
            workspace_id=workspace_id,
            status="succeeded",
            items=[
                RetrievalResultItem(
                    paper_id="paper-1",
                    paper_title="Grounded Paper",
                    chunk_id="chunk-1",
                    section="Methods",
                    text="base contrastive learning",
                    score=0.7,
                )
            ],
        )

    monkeypatch.setattr("app.domains.agent.service.semantic_search", failed)
    service = AgentService(db_session)
    fallback = [{"evidence_id": "E1", "chunk_id": "chunk-1", "text": "base"}]
    result = service._code_rag_evidence(run, plan, fallback)
    assert result == fallback
    assert not any("facets" in entry for entry in result)  # fallback untouched


def test_code_agent_survives_single_file_generation_failure(
    client, db_session: Session, monkeypatch
):
    workspace, conversation = _workspace_conversation(client)
    plan = ResearchPlan(
        workspace_id=workspace["id"],
        opportunity_id=None,
        opportunity_version_id=None,
        source_type="agent",
        status="draft",
        research_question="Q",
        hypothesis="H",
        scope_and_assumptions="",
        datasets=[],
        baselines=[],
        metrics=[],
        validation_steps=[],
        expected_supporting_result="",
        falsification_criteria="",
        risks=[],
        resource_constraints="",
    )
    db_session.add(plan)
    db_session.commit()
    monkeypatch.setattr(
        "app.domains.agent.service.semantic_search", lambda **_: _retrieval(workspace["id"])
    )
    with patch("app.domains.agent.router.spawn_agent_task", return_value="celery-code"):
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/agent-runs",
            json={
                "agent_type": "code_generation",
                "prompt": "生成最小实验",
                "conversation_id": conversation["id"],
                "input": {"research_plan_id": plan.id},
            },
        )
    run_id = created.json()["id"]
    gateway = FakeGateway(
        blueprint_payload={
            "summary": "最小项目",
            "modules": [],
            "files": [
                {
                    "path": "README.md",
                    "language": "markdown",
                    "purpose": "说明",
                    "depends_on": [],
                    "evidence_refs": [],
                },
                {
                    "path": "src/train.py",
                    "language": "python",
                    "purpose": "训练入口",
                    "depends_on": [],
                    "evidence_refs": [],
                },
            ],
            "entrypoint": "src/train.py",
            "test_files": [],
        },
        payload={
            "files": [
                {"path": "src/train.py", "language": "python", "content": "print('train')"},
            ],
        },
        fail_paths={"README.md"},
    )
    AgentService(db_session, gateway=gateway).execute(run_id)
    detail = client.get(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{run_id}").json()
    assert detail["status"] == "succeeded"
    # README generation kept failing (invalid JSON): run survives with the rest
    assert {item["filename"] for item in detail["artifacts"] if item["artifact_type"] == "code"} == {
        "src/train.py", "requirements.txt"
    }
    assert "README.md" in {g["path"] for g in detail["result"]["file_errors"]}
    # ZIP contains only the successfully generated files, never a placeholder
    bundle = client.get(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{run_id}/bundle")
    with zipfile.ZipFile(io.BytesIO(bundle.content)) as zf:
        names = zf.namelist()
        assert "src/train.py" in names
        assert "README.md" not in names


def test_agent_workspace_isolation(
    client, db_session: Session
):
    workspace, conversation = _workspace_conversation(client)
    other = client.post("/api/v1/workspaces", json={"name": "Other"}).json()
    plan = ResearchPlan(
        workspace_id=workspace["id"],
        opportunity_id=None,
        opportunity_version_id=None,
        source_type="agent",
        status="draft",
        research_question="Q",
        hypothesis="H",
        scope_and_assumptions="",
        datasets=[],
        baselines=[],
        metrics=[],
        validation_steps=[],
        expected_supporting_result="",
        falsification_criteria="",
        risks=[],
        resource_constraints="",
    )
    db_session.add(plan)
    db_session.commit()
    with patch("app.domains.agent.router.spawn_agent_task", return_value="celery-code"):
        created = client.post(
            f"/api/v1/workspaces/{workspace['id']}/agent-runs",
            json={
                "agent_type": "code_generation",
                "prompt": "code",
                "conversation_id": conversation["id"],
                "input": {"research_plan_id": plan.id},
            },
        ).json()
    assert (
        client.get(f"/api/v1/workspaces/{other['id']}/agent-runs/{created['id']}").status_code
        == 404
    )
    # the code-execution sandbox was removed (08-19): there is no validate
    # endpoint anymore, so an unknown path stays 404
    assert (
        client.post(f"/api/v1/workspaces/{workspace['id']}/agent-runs/{created['id']}/validate")
        .status_code
        == 404
    )
