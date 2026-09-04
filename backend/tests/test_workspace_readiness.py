"""W0 研究就绪度测试。

直接针对 ORM 初始化的 SQLite session 覆盖 WorkspaceReadinessService（五个维度 + recommended
next action），并覆盖 HTTP endpoint。

Milvus chunk count 有意采用 best-effort；测试用 fake 替换它，使 `count_by_workspace` 返回
已知值（并证明 Milvus outage 会降级为 None）。
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.domains.discover.models import DiscoverRun, ResearchOpportunity, ResearchPlan
from app.domains.knowledge.models import KnowledgeItem
from app.domains.paper.models import Paper
from app.domains.task.models import Task
from app.domains.workspace.models import Workspace
from app.domains.workspace.readiness import WorkspaceReadinessService

# ------------------------------------------------------------------------- 辅助函数


@pytest.fixture
def fake_milvus(monkeypatch):
    """替换 MilvusClient，使 readiness 分块计数快速且确定。"""

    class FakeMilvus:
        def __init__(self, **kwargs):
            pass

        def count_by_workspace(self, workspace_id):
            return 42

    monkeypatch.setattr("app.domains.retrieval.milvus_client.MilvusClient", FakeMilvus)
    return FakeMilvus


def _ws(db: Session, **kw) -> Workspace:
    data = {
        "id": str(uuid4()),
        "name": "测试课题",
        "topic": "GNN explanation robustness",
        "goals": "验证反例稳定性",
        "active_questions": ["分布偏移下解释是否稳定?"],
    }
    data.update(kw)
    ws = Workspace(**data)
    db.add(ws)
    db.commit()
    db.refresh(ws)
    return ws


def _paper(db: Session, ws: Workspace, *, parse_status="parsed", extract_status="extracted") -> Paper:
    p = Paper(
        id=str(uuid4()),
        workspace_id=ws.id,
        title="Paper",
        parse_status=parse_status,
        extract_status=extract_status,
        primary_artifact_id="artifact-1" if parse_status != "not_applicable" else None,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _knowledge(db: Session, ws: Workspace, *, status="extracted_candidate") -> KnowledgeItem:
    k = KnowledgeItem(
        id=str(uuid4()),
        workspace_id=ws.id,
        type="claim",
        canonical_name="Claim",
        status=status,
    )
    db.add(k)
    db.commit()
    db.refresh(k)
    return k


def _run(db: Session, ws: Workspace, *, status="succeeded") -> DiscoverRun:
    r = DiscoverRun(id=str(uuid4()), workspace_id=ws.id, status=status)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _opportunity(db: Session, ws: Workspace, *, status="candidate") -> ResearchOpportunity:
    o = ResearchOpportunity(
        id=str(uuid4()),
        workspace_id=ws.id,
        title="Opportunity",
        summary="summary",
        rationale="rationale",
        status=status,
    )
    db.add(o)
    db.commit()
    db.refresh(o)
    return o


def _plan(db: Session, ws: Workspace) -> ResearchPlan:
    p = ResearchPlan(
        id=str(uuid4()),
        workspace_id=ws.id,
        research_question="RQ",
        hypothesis="H",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _readiness(db: Session, ws: Workspace) -> dict:
    return WorkspaceReadinessService(db).get_readiness(ws)


def _dim(readiness: dict, key: str) -> dict:
    return next(d for d in readiness["dimensions"] if d["key"] == key)


# ------------------------------------------------------------------- 维度状态


def test_empty_workspace_all_blocked_and_recommends_add_papers(db_session: Session, fake_milvus):
    ws = _ws(db_session)
    r = _readiness(db_session, ws)

    assert all(d["ready"] is False for d in r["dimensions"])
    corpus = _dim(r, "corpus")
    assert corpus["blocking_actions"][0]["action"] == "添加论文"
    assert corpus["blocking_actions"][0]["href"].endswith("/papers")

    rec = r["recommended_next_action"]
    assert rec["title"] == "添加论文"
    assert rec["href"].endswith("/papers")


def test_corpus_ready_with_parsed_paper(db_session: Session, fake_milvus):
    ws = _ws(db_session)
    _paper(db_session, ws, extract_status="not_applicable")  # parsed but not extracted
    r = _readiness(db_session, ws)

    assert _dim(r, "corpus")["ready"] is True
# retrieval 尚未就绪（没有抽取内容），其阻塞步骤指向活动状态
    retrieval = _dim(r, "retrieval")
    assert retrieval["ready"] is False
    assert retrieval["blocking_actions"][0]["href"].endswith("/activity")
    assert r["recommended_next_action"]["href"].endswith("/activity")


def test_retrieval_knowledge_discover_ready_flow(db_session: Session, fake_milvus):
    ws = _ws(db_session)
    _paper(db_session, ws)          # parsed + extracted
    _knowledge(db_session, ws, status="human_confirmed")
    r = _readiness(db_session, ws)

    assert _dim(r, "retrieval")["ready"] is True
    assert _dim(r, "knowledge")["ready"] is True
    assert _dim(r, "discover")["ready"] is True  # profile_set + retrieval + knowledge
    assert _dim(r, "research")["ready"] is False
# 尚无已确认 opportunity -> 建议运行 Discover
    rec = r["recommended_next_action"]
    assert rec["title"] == "运行 Discover 并确认机会"
    assert rec["href"].endswith("/discover")


def test_recommend_review_knowledge_when_none_confirmed(db_session: Session, fake_milvus):
    """运行 Discover 前展示尚未审核的抽取知识。"""
    ws = _ws(db_session)
    _paper(db_session, ws)
    _knowledge(db_session, ws, status="extracted_candidate")  # not yet reviewed
    r = _readiness(db_session, ws)

    assert _dim(r, "discover")["ready"] is True
    rec = r["recommended_next_action"]
    assert rec["title"] == "审核确认知识"
    assert rec["href"].endswith("/knowledge")


def test_research_ready_and_all_green(db_session: Session, fake_milvus):
    ws = _ws(db_session)
    _paper(db_session, ws)
    _knowledge(db_session, ws, status="human_confirmed")
    _opportunity(db_session, ws, status="confirmed")
    r = _readiness(db_session, ws)

    assert all(d["ready"] for d in r["dimensions"])
# 已确认 opportunity 不处于 pending -> 建议进入 research center
    rec = r["recommended_next_action"]
    assert rec["title"] == "进入研究中心"
    assert rec["href"].endswith("/plans")


def test_pending_opportunity_priority(db_session: Session, fake_milvus):
    ws = _ws(db_session)
    _paper(db_session, ws)
    _knowledge(db_session, ws, status="human_confirmed")
    _opportunity(db_session, ws, status="candidate")  # pending, not confirmed
    r = _readiness(db_session, ws)

    assert _dim(r, "discover")["ready"] is True
    assert _dim(r, "research")["ready"] is False
    rec = r["recommended_next_action"]
    assert rec["title"] == "处理待确认机会"
    assert rec["href"].endswith("/discover")


def test_waiting_state_with_active_pipeline_task(db_session: Session, fake_milvus):
    ws = _ws(db_session)
    _paper(db_session, ws, parse_status="pending", extract_status="not_applicable")
    db_session.add(
        Task(id=str(uuid4()), workspace_id=ws.id, task_type="parse_pdf", status="queued")
    )
    db_session.commit()
    r = _readiness(db_session, ws)

    corpus = _dim(r, "corpus")
    assert corpus["ready"] is False
    assert corpus["waiting"] is True
    assert corpus["blocking_actions"] == []
# waiting 维度 -> 建议进入 activity center，而不是给出阻塞操作
    rec = r["recommended_next_action"]
    assert rec["title"] == "查看处理进度"
    assert rec["href"].endswith("/activity")


def test_discover_blocked_without_research_profile(db_session: Session, fake_milvus):
    ws = _ws(db_session, topic="", goals="", active_questions=[])
    _paper(db_session, ws)
    _knowledge(db_session, ws)
    r = _readiness(db_session, ws)

    discover = _dim(r, "discover")
    assert discover["ready"] is False
    assert any(b["action"] == "设置研究主题与问题" for b in discover["blocking_actions"])
    assert r["recommended_next_action"]["href"].endswith("/settings")


def test_pending_run_marks_discover_waiting(db_session: Session, fake_milvus):
    ws = _ws(db_session)
    _paper(db_session, ws)
    _knowledge(db_session, ws)
    _run(db_session, ws, status="waiting_for_user")
    r = _readiness(db_session, ws)

# discover 已就绪；等待中的 run 仍报告 waiting（信息性状态）
    assert _dim(r, "discover")["ready"] is True
    assert r["counts"]["pending_runs"] == 1


# ------------------------------------------------------------------------ 计数


def test_counts_accuracy(db_session: Session, fake_milvus):
    ws = _ws(db_session)
    _paper(db_session, ws, parse_status="parsed", extract_status="extracted")
    _paper(db_session, ws, parse_status="not_applicable", extract_status="not_applicable")
    _knowledge(db_session, ws, status="human_confirmed")
    _knowledge(db_session, ws, status="extracted_candidate")
    _run(db_session, ws, status="succeeded")
    _run(db_session, ws, status="waiting_for_user")
    _opportunity(db_session, ws, status="confirmed")
    _opportunity(db_session, ws, status="candidate")
    _plan(db_session, ws)
    r = _readiness(db_session, ws)

    c = r["counts"]
    assert c["papers"] == 2
    assert c["papers_with_pdf"] == 1
    assert c["parsed_papers"] == 1
    assert c["extracted_papers"] == 1
    assert c["knowledge_items"] == 2
    assert c["confirmed_items"] == 1
    assert c["pending_knowledge"] == 1
    assert c["runs"] == 2
    assert c["pending_runs"] == 1
    assert c["opportunities"] == 2
    assert c["pending_opportunities"] == 1
    assert c["confirmed_opportunities"] == 1
    assert c["research_plans"] == 1
    assert c["active_tasks"] == 0


def test_milvus_outage_degrades_chunk_count(db_session: Session, monkeypatch):
    """Milvus 失败不能破坏 readiness，分块数量降级为 None。"""

    def boom(*args, **kwargs):
        raise RuntimeError("milvus down")

    monkeypatch.setattr(
        "app.domains.retrieval.milvus_client.MilvusClient", lambda **kw: type("X", (), {"count_by_workspace": boom})()
    )
    ws = _ws(db_session)
    r = _readiness(db_session, ws)
    assert r["counts"]["papers"] == 0  # still works
    assert all(d["ready"] is False for d in r["dimensions"])
    assert r["recommended_next_action"]["title"] == "添加论文"


# ------------------------------------------------------------------------- HTTP 接口


def test_readiness_endpoint(client, db_session: Session, fake_milvus):
    ws = _ws(db_session)
    _paper(db_session, ws)
    _knowledge(db_session, ws, status="human_confirmed")
    resp = client.get(f"/api/v1/workspaces/{ws.id}/readiness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["workspace_id"] == ws.id
    keys = [d["key"] for d in body["dimensions"]]
    assert keys == ["corpus", "retrieval", "knowledge", "discover", "research"]
    assert body["counts"]["papers"] == 1
    assert body["recommended_next_action"]["href"].endswith("/discover")


def test_readiness_endpoint_404_for_missing_workspace(client):
    resp = client.get("/api/v1/workspaces/does-not-exist/readiness")
    assert resp.status_code == 404
