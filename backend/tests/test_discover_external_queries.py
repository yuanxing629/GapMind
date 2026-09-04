"""Stage 3 外部 query 构建和多 query 合并测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.domains.discover.models import DiscoverRun  # noqa: E402
from app.domains.discover.external_retrieval import EXTERNAL_QUERY_MAX_TOTAL  # noqa: E402
from app.domains.discover.service import DiscoverService  # noqa: E402
from app.domains.knowledge.models import KnowledgeItem  # noqa: E402
from app.gateway.semantic_scholar import SemanticScholarError  # noqa: E402
from app.domains.workspace.models import Workspace  # noqa: E402


def _run(workspace_id: str, **overrides: Any) -> DiscoverRun:
    kwargs = {
        "id": str(uuid4()),
        "workspace_id": workspace_id,
        "trigger_type": "topic",
        "input_topic": "GNN interpretability under distribution shift",
        "input_payload": {"topic": "GNN interpretability under distribution shift", "keywords": []},
        "scope": {},
        "config": {"top_k": 10},
        "status": "running",
        "stage": "external_search",
        "progress": 0.5,
        "verification_status": "in_progress",
        "stage_summaries": {},
    }
    kwargs.update(overrides)
    return DiscoverRun(**kwargs)


def _knowledge_item(
    workspace_id: str,
    type_: str,
    name: str,
    *,
    confidence: float = 0.8,
    status: str = "extracted_candidate",
    content: dict[str, Any] | None = None,
) -> KnowledgeItem:
    return KnowledgeItem(
        id=str(uuid4()),
        workspace_id=workspace_id,
        type=type_,
        canonical_name=name,
        content=content or {},
        confidence=confidence,
        status=status,
        is_deleted=False,
        created_by="agent",
    )


class _S2Fake:
    """预设的逐 query Semantic Scholar 结果。"""

    def __init__(self, per_query: dict[str, list[dict[str, Any]]]) -> None:
        self.per_query = per_query
        self.calls: list[dict[str, Any]] = []

    def search(self, query: str, *, fields: str, **kw: Any):
        self.calls.append({"query": query, **kw})
        return {"data": self.per_query.get(query, []), "total": len(self.per_query.get(query, []))}

    def get_paper(self, paper_id: str, *, fields: str):
        return {"paperId": paper_id}


def _s2_paper(pid: str, title: str, **extra: Any) -> dict[str, Any]:
    return {"paperId": pid, "title": title, "abstract": "abstract", **extra}


class _NoopLLM:
    def __init__(self) -> None:
        self.messages: list[list[dict[str, str]]] = []

    def chat_completion(self, messages, **kwargs):
        self.messages.append(messages)
        return SimpleNamespace(content=json.dumps({"roles": []}))


class _AxisLLM:
    """为 query-generation 调用返回研究轴 query（以及可选的精确查找）。"""

    def __init__(self, queries: list[str], exact_lookups: list[str] | None = None) -> None:
        self.queries = queries
        self.exact_lookups = exact_lookups or []
        self.messages: list[list[dict[str, str]]] = []

    def chat_completion(self, messages, **kwargs):
        self.messages.append(messages)
        payload = {"queries": self.queries, "exact_lookups": self.exact_lookups}
        return SimpleNamespace(content=json.dumps(payload))


class _BoomLLM:
    def chat_completion(self, messages, **kwargs):
        raise RuntimeError("llm down")


def _service(db_session, external_search, llm=None) -> DiscoverService:
    return DiscoverService(
        db_session,
        external_search=external_search,
        llm=llm or _NoopLLM(),
    )


# ------------------------------------------------------------------ 构建 queries
def test_build_external_queries_primary_then_keywords(db_session) -> None:
    workspace_id = str(uuid4())
    run = _run(workspace_id, input_payload={"keywords": ["invariant rationalization", "counterfactual"]})
    service = DiscoverService(db_session, external_search=_S2Fake({}), llm=_NoopLLM())
    queries = service._build_external_queries(run, "My research claim")
    assert queries[0] == "My research claim"
    assert "invariant rationalization" in queries
    assert "counterfactual" in queries
# 去重 + 上限
    assert len(queries) == len(set(q.lower() for q in queries))
    assert len(queries) <= EXTERNAL_QUERY_MAX_TOTAL


def test_build_external_queries_adds_workspace_signals(db_session) -> None:
    workspace_id = str(uuid4())
    db_session.add_all(
        [
            _knowledge_item(workspace_id, "method", "PGIB", confidence=0.9, content={"description": "invariant rationale extraction"}),
            _knowledge_item(workspace_id, "claim", "IB improves OOD generalization", confidence=0.8, content={"statement": "IB improves OOD generalization"}),
            _knowledge_item(workspace_id, "task", "OOD detection", confidence=0.7),
# rejected 和低置信度条目必须跳过
            _knowledge_item(workspace_id, "method", "NoisyMethod", confidence=0.9, status="rejected"),
            _knowledge_item(workspace_id, "method", "LowConf", confidence=0.1),
        ]
    )
    db_session.commit()
    run = _run(workspace_id)
    service = DiscoverService(db_session, external_search=_S2Fake({}), llm=_NoopLLM())
    queries = service._build_external_queries(run, "Primary")
    assert queries[0] == "Primary"
    joined = " | ".join(queries)
    assert "IB improves OOD generalization" in joined  # claim signal
    assert "PGIB" in joined  # method signal
    assert "NoisyMethod" not in joined
    assert "LowConf" not in joined
    assert len(queries) <= EXTERNAL_QUERY_MAX_TOTAL


def test_external_query_text_renders_by_type(db_session) -> None:
    workspace_id = str(uuid4())
    service = DiscoverService(db_session, external_search=_S2Fake({}), llm=_NoopLLM())
# 多词 method name 原样使用
    gnn = _knowledge_item(workspace_id, "method", "Graph Information Bottleneck", content={"description": "..."})
    assert service._external_query_text(gnn) == "Graph Information Bottleneck"
# 混合大小写 method name 原样使用
    pgib = _knowledge_item(workspace_id, "method", "PGIB", content={"description": "invariant rationale"})
    assert service._external_query_text(pgib) == "PGIB"
# 全大写缩写从 description 的首个名词短语展开
    irm = _knowledge_item(workspace_id, "method", "IRM", content={"description": "Invariant Risk Minimization finds a representation"})
    assert service._external_query_text(irm) == "Invariant Risk Minimization"
# claim 渲染为其 statement
    claim = _knowledge_item(workspace_id, "claim", "same", content={"statement": "IB improves OOD generalization"})
    assert service._external_query_text(claim) == "IB improves OOD generalization"
    task = _knowledge_item(workspace_id, "task", "OOD detection", content={})
    assert service._external_query_text(task) == "OOD detection"


def test_external_query_signal_items_orders_and_filters(db_session) -> None:
    workspace_id = str(uuid4())
    db_session.add_all(
        [
            _knowledge_item(workspace_id, "method", "Method", confidence=0.9),
            _knowledge_item(workspace_id, "claim", "Claim", confidence=0.6),
            _knowledge_item(workspace_id, "method", "Rejected", confidence=0.9, status="rejected"),
            _knowledge_item(workspace_id, "method", "Low", confidence=0.2),
        ]
    )
    db_session.commit()
    service = DiscoverService(db_session, external_search=_S2Fake({}), llm=_NoopLLM())
    items = service._external_query_signal_items(workspace_id)
    names = [it.canonical_name for it in items]
    assert names == ["Method", "Claim"]  # methods before claims, filtered
    assert "Rejected" not in names
    assert "Low" not in names


# ------------------------------------------------------------------ 研究轴 queries（LLM）
def test_axis_queries_from_llm_parses_response(db_session) -> None:
    workspace_id = str(uuid4())
    llm = _AxisLLM(["graph information bottleneck", "explanation stability neural networks"])
    service = _service(db_session, _S2Fake({}), llm)
    run = _run(workspace_id)
    out, lookups = service._axis_queries_from_llm(run, "My research question")
    assert out == ["graph information bottleneck", "explanation stability neural networks"]
    assert lookups == []
# 在 user prompt 中携带研究问题，并只调用一次 LLM
    assert len(llm.messages) == 1
    assert "My research question" in llm.messages[0][-1]["content"]


def test_axis_queries_from_llm_parses_exact_lookups(db_session) -> None:
    workspace_id = str(uuid4())
    llm = _AxisLLM(["graph information bottleneck"], exact_lookups=["Invariant Risk Minimization", "Graph Information Bottleneck"])
    service = _service(db_session, _S2Fake({}), llm)
    run = _run(workspace_id)
    out, lookups = service._axis_queries_from_llm(run, "My research question")
    assert out == ["graph information bottleneck"]
    assert lookups == ["Invariant Risk Minimization", "Graph Information Bottleneck"]


def test_build_external_queries_uses_llm_axis_queries(db_session) -> None:
    workspace_id = str(uuid4())
    llm = _AxisLLM(["graph information bottleneck", "explanation stability", "saliency robustness"])
    service = _service(db_session, _S2Fake({}), llm)
    run = _run(workspace_id)
    queries = service._build_external_queries(run, "Primary claim")
    assert queries[0] == "Primary claim"
    for axis in ("graph information bottleneck", "explanation stability", "saliency robustness"):
        assert axis in queries
    assert len(queries) <= EXTERNAL_QUERY_MAX_TOTAL


def test_build_external_queries_falls_back_when_llm_bad_shape(db_session) -> None:
    workspace_id = str(uuid4())
    db_session.add(_knowledge_item(workspace_id, "method", "PGIB", confidence=0.9))
    db_session.commit()
# _NoopLLM 返回 {"roles": []}，不符合 axis query 结构 -> 回退
    service = _service(db_session, _S2Fake({}), _NoopLLM())
    run = _run(workspace_id)
    queries = service._build_external_queries(run, "Primary")
    assert queries[0] == "Primary"
    assert "PGIB" in queries  # workspace-signal fallback


def test_build_external_queries_llm_failure_keeps_workspace_signals(db_session) -> None:
    workspace_id = str(uuid4())
    db_session.add(_knowledge_item(workspace_id, "method", "SubgraphX", confidence=0.9))
    db_session.commit()
    service = _service(db_session, _S2Fake({}), _BoomLLM())
    run = _run(workspace_id)
    queries = service._build_external_queries(run, "Primary")
    assert queries[0] == "Primary"
    assert "SubgraphX" in queries  # graceful fallback


def test_external_query_signal_texts_renders_compact(db_session) -> None:
    workspace_id = str(uuid4())
    db_session.add_all(
        [
            _knowledge_item(workspace_id, "method", "Graph Information Bottleneck", confidence=0.9),
            _knowledge_item(workspace_id, "method", "IRM", confidence=0.9, content={"description": "Invariant Risk Minimization finds a representation"}),
            _knowledge_item(workspace_id, "limitation", "Existing methods ignore stability", confidence=0.9),
        ]
    )
    db_session.commit()
    service = DiscoverService(db_session, external_search=_S2Fake({}), llm=_NoopLLM())
    text = service._external_query_signal_texts(workspace_id)
    assert "Methods:" in text
    assert "Graph Information Bottleneck" in text
    assert "Invariant Risk Minimization" in text
    assert "Limitations:" in text
    assert "Existing methods ignore stability" in text


# ------------------------------------------------------------------ 合并与去重
def test_external_verify_merges_and_dedupes_candidates(db_session) -> None:
    workspace_id = str(uuid4())
    primary = "GNN interpretability"
    fake = _S2Fake(
        {
            primary: [
                _s2_paper("p1", "Paper One", authors=[{"name": "A"}], year=2020),
                _s2_paper("p2", "Paper Two"),
                _s2_paper("p3", "Paper Three"),
            ],
            "PGIB: invariant rationale": [
                _s2_paper("p2", "Paper Two"),  # duplicate across queries
                _s2_paper("p4", "Paper Four"),
            ],
        }
    )
    llm = _NoopLLM()
    service = _service(db_session, fake, llm)
    run = _run(workspace_id)
    db_session.add_all([Workspace(id=workspace_id, name="External search workspace", is_archived=False), run])
    db_session.commit()
    count = service._external_verify(run, [primary, "PGIB: invariant rationale"])

    assert count == 4  # p1..p4 deduped, no duplicate p2
    from app.domains.discover.models import DiscoverExternalCandidate

    cands = (
        db_session.query(DiscoverExternalCandidate)
        .filter(DiscoverExternalCandidate.discover_run_id == run.id)
        .order_by(DiscoverExternalCandidate.rank)
        .all()
    )
# RRF fusion（P2-4）：p2 被两个 query 同时召回，因此无论位置如何都超过所有
# 单 query 命中；单 query 命中再按 query 内位置排序（p1 pos1 < p4 pos2 < p3 pos3）。
    assert [c.external_paper_id for c in cands] == ["p2", "p1", "p4", "p3"]
    assert [c.rank for c in cands] == [1, 2, 3, 4]
# 每个候选记录召回它的 query
    assert cands[1].query == primary  # p1 only in the primary query
    assert cands[0].query == "PGIB: invariant rationale"  # p2 first seen under the extra query
    assert cands[3].query == primary  # p3 only in the primary query
    assert fake.calls[0]["query"] == primary
    assert fake.calls[0]["limit"] == 10  # every query fetches full top_k (P2-4)
    assert fake.calls[1]["limit"] == 10  # extra queries too: recall is capped by truncation
    assert run.stage_summaries["external_search"]["candidate_count"] == 4
    summary = dict(run.stage_summaries["external_search"])
    service._stage(run, "external_search", 0.58, {**summary, "external_candidates": count})
    assert run.stage_summaries["external_search"]["status"] == "succeeded"
    assert run.stage_summaries["external_search"]["executed"] is True
# role judge 针对研究问题（primary）运行，而不是额外 query
    assert llm.messages
    assert primary in llm.messages[0][-1]["content"]
    assert "PGIB" not in llm.messages[0][-1]["content"].split("CANDIDATES:")[0]


def test_external_verify_empty_queries(db_session) -> None:
    workspace_id = str(uuid4())
    fake = _S2Fake({})
    service = _service(db_session, fake)
    run = _run(workspace_id)
    count = service._external_verify(run, ["   ", ""])
    assert count == 0
    assert run.verification_status == "incomplete"
    assert fake.calls == []


def test_external_verify_semantic_scholar_failure(db_session) -> None:
    workspace_id = str(uuid4())

    class _BoomS2:
        def search(self, query: str, *, fields: str, **kw: Any):
            raise SemanticScholarError(status_code=429, message="rate limited")

        def get_paper(self, paper_id: str, *, fields: str):
            raise SemanticScholarError(status_code=429, message="rate limited")

    service = _service(db_session, _BoomS2())
    run = _run(workspace_id)
    count = service._external_verify(run, ["claim"])
    assert count == 0
    assert run.verification_status == "failed"
    assert run.stage_summaries["external_search"]["status"] == "failed"
    assert run.stage_summaries["external_search"]["retryable"] is True


def test_external_verify_preserves_successes_when_a_later_query_fails(db_session) -> None:
    workspace_id = str(uuid4())
    primary = "GNN interpretability"

    class _PartialS2:
        def search(self, query: str, *, fields: str, **kw: Any):
            if query == "rate limited query":
                raise SemanticScholarError(status_code=429, message="rate limited")
            return {"data": [_s2_paper("p1", "Paper One")], "total": 1}

        def get_paper(self, paper_id: str, *, fields: str):
            return {"paperId": paper_id}

    service = _service(db_session, _PartialS2())
    run = _run(workspace_id)
    db_session.add_all([Workspace(id=workspace_id, name="Partial search", is_archived=False), run])
    db_session.commit()

    count = service._external_verify(run, [primary, "rate limited query"])

    assert count == 1
    summary = run.stage_summaries["external_search"]
    assert summary["status"] == "succeeded_partial"
    assert summary["executed"] is True
    assert summary["successful_query_count"] == 1
    assert summary["failed_query_count"] == 1
    assert summary["query_failures"][0]["status_code"] == 429
    assert summary["query_failures"][0]["failure_kind"] == "rate_limited"
    assert summary["failure_counts"] == {"rate_limited": 1}


def test_external_verify_keeps_non_critical_partial_search_informational(db_session) -> None:
    workspace_id = str(uuid4())

    class _MostlyHealthyS2:
        def search(self, query: str, *, fields: str, **kw: Any):
            if query == "limited query":
                raise SemanticScholarError(status_code=429, message="rate limited")
            if query == "Unavailable Method":
                raise SemanticScholarError(status_code=503, message="temporarily unavailable")
            return {"data": [_s2_paper(f"p-{len(query)}-{query[-1:]}", f"Paper {query}")]}

        def get_paper(self, paper_id: str, *, fields: str):
            return {"paperId": paper_id}

    service = _service(db_session, _MostlyHealthyS2())
    run = _run(workspace_id)
    db_session.add_all([Workspace(id=workspace_id, name="Mostly healthy search", is_archived=False), run])
    db_session.commit()
    queries = ["Primary question"] + [f"method query {index}" for index in range(1, 11)] + ["limited query"]

    count = service._external_verify(run, queries, exact_lookups=["Unavailable Method"])

    assert count >= 2
    summary = run.stage_summaries["external_search"]
    assert summary["status"] == "succeeded"
    assert summary["notice_level"] == "informational"
    assert summary["impact"] == "non_critical_query_limited"
    assert summary["successful_query_count"] == 11
    assert summary["failed_query_count"] == 1
    assert abs(summary["query_success_rate"] - 11 / 12) < 1e-4
    assert summary["exact_lookup_failure_count"] == 1
    records = {record["query"]: record for record in summary["query_records"]}
    assert records["Primary question"]["purpose"] == "primary_question"
    assert records["limited query"]["purpose"] == "method_overlap"
    assert records["Unavailable Method"]["purpose"] == "exact_lookup"
    assert records["Unavailable Method"]["status"] == "failed"
    assert records["Unavailable Method"]["failure_kind"] == "upstream_error"


def test_external_verify_marks_primary_failure_as_warning(db_session) -> None:
    workspace_id = str(uuid4())

    class _PrimaryFailureS2:
        def search(self, query: str, *, fields: str, **kw: Any):
            if query == "Primary question":
                raise SemanticScholarError(status_code=429, message="rate limited")
            return {"data": [_s2_paper("p1", "Paper One"), _s2_paper("p2", "Paper Two")]}

        def get_paper(self, paper_id: str, *, fields: str):
            return {"paperId": paper_id}

    service = _service(db_session, _PrimaryFailureS2())
    run = _run(workspace_id)
    db_session.add_all([Workspace(id=workspace_id, name="Primary failure", is_archived=False), run])
    db_session.commit()

    count = service._external_verify(run, ["Primary question", "counter evidence critique"])

    assert count == 2
    summary = run.stage_summaries["external_search"]
    assert summary["status"] == "succeeded_partial"
    assert summary["notice_level"] == "warning"
    assert summary["impact"] == "critical_query_failed"
    assert summary["query_failures"][0]["purpose"] == "primary_question"
    assert summary["query_records"][1]["purpose"] == "counter_evidence"


# ------------------------------------------------------------------ 精确名称查找
def test_title_verified_matches_words(db_session) -> None:
    service = DiscoverService(db_session, external_search=_S2Fake({}), llm=_NoopLLM())
    assert service._title_verified("Graph Information Bottleneck", "Graph Information Bottleneck")
    assert service._title_verified("Invariant Risk Minimization", "Invariant Risk Minimization")
    assert not service._title_verified("Graph Information Bottleneck", "Some Unrelated Paper")
    assert not service._title_verified("GIB", "Graph Information Bottleneck")  # too few words


def test_external_verify_precise_lookup_prepends_verified(db_session) -> None:
    workspace_id = str(uuid4())
    primary = "GNN interpretability"
    fake = _S2Fake(
        {
            primary: [_s2_paper("p1", "Paper One")],
            "other query": [_s2_paper("p2", "Paper Two")],
            "Graph Information Bottleneck": [_s2_paper("pGIB", "Graph Information Bottleneck", year=2020)],
            "Unrelated Name": [_s2_paper("pX", "Something Completely Different")],
        }
    )
    service = _service(db_session, fake, _NoopLLM())
    run = _run(workspace_id)
    count = service._external_verify(
        run,
        [primary, "other query"],
        exact_lookups=["Graph Information Bottleneck", "Unrelated Name"],
    )
    from app.domains.discover.models import DiscoverExternalCandidate

    cands = (
        db_session.query(DiscoverExternalCandidate)
        .filter(DiscoverExternalCandidate.discover_run_id == run.id)
        .order_by(DiscoverExternalCandidate.rank)
        .all()
    )
# 已验证查找结果置顶；未匹配查找跳过。
    assert [c.external_paper_id for c in cands] == ["pGIB", "p1", "p2"]
    assert cands[0].query == "exact: Graph Information Bottleneck"
    assert count == 3
# Lookup 使用精确名称额外发起一次 S2 调用。
    assert any(call["query"] == "Graph Information Bottleneck" for call in fake.calls)


def test_axis_query_prompt_requires_counter_evidence_and_evaluation_axes(db_session) -> None:
    """P2-4：轴分解提示词必须硬性要求反证和评测角度。

    没有该要求时 LLM 会跳过这些角度，外部门禁会完全漏掉批评性文献（召回率 0.286 的案例）。"""
    workspace_id = str(uuid4())
    llm = _AxisLLM(["graph information bottleneck", "explanation stability"])
    service = _service(db_session, _S2Fake({}), llm)
    run = _run(workspace_id)
    service._axis_queries_from_llm(run, "Are explanations faithful and stable?")
    user_prompt = llm.messages[0][-1]["content"]
    assert "COUNTER-EVIDENCE" in user_prompt
    assert "EVALUATION" in user_prompt
    assert "mandatory" in user_prompt
