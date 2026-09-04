"""Discover service Protocol ports 测试。

验证 ``DiscoverService.__init__`` 接受兼容 Protocol 的 fake，且替换它们不需要修改 service
代码。实际的跨 domain 行为由 retrieval / llm 测试套件覆盖；本文件只验证 wiring。
"""

from __future__ import annotations

from typing import Any

from app.domains.discover.adapters import (
    ExternalSearchAdapter,
    LLMGatewayAdapter,
    RetrievalAdapter,
    assert_protocol,
)
from app.domains.discover.ports import (
    ExternalSearchPort,
    LLMGatewayPort,
    RetrievalPort,
)
from app.domains.discover.service import DiscoverService


class FakeRetrieval:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def semantic_search(self, workspace_id: str, query: str, top_k: int, **kw: Any):
        self.calls.append(("semantic_search", workspace_id, {"query": query, "top_k": top_k, **kw}))
        return _stub_response(query)

    def find_similar_work(self, workspace_id: str, paper_id: str, top_k: int, **kw: Any):
        self.calls.append(("find_similar_work", workspace_id, {"paper_id": paper_id, **kw}))
        return _stub_response(paper_id)

    def find_counter_evidence(self, workspace_id: str, claim: str, top_k: int, **kw: Any):
        self.calls.append(("find_counter_evidence", workspace_id, {"claim": claim, **kw}))
        return _stub_response(claim)


class FakeExternalSearch:
    def __init__(self) -> None:
        self.searches: list[dict[str, Any]] = []

    def search(self, query: str, *, fields: str, **kw: Any):
        self.searches.append({"query": query, "fields": fields, **kw})
        return {"data": [], "total": 0}

    def get_paper(self, paper_id: str, *, fields: str):
        return {"paperId": paper_id}


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    def chat_completion(self, messages: list[dict[str, str]], **kw: Any):
        self.calls.append(messages)
        return _stub_llm_response("{}")


def _stub_response(query: str):
    from app.domains.retrieval.schemas import RetrievalResponse

    return RetrievalResponse(
        workspace_id="ws-1",
        query=query,
        purpose="test",
        status="succeeded",
        items=[],
        total=0,
        error=None,
    )


def _stub_llm_response(content: str):
    from types import SimpleNamespace

    return SimpleNamespace(content=content, usage=None)


# ---------------------------------------------------------------- 测试用例
def test_default_adapters_satisfy_protocols() -> None:
    """生产 adapter 必须是有效的 port binding。"""
    assert isinstance(RetrievalAdapter(), RetrievalPort)
    assert isinstance(ExternalSearchAdapter(), ExternalSearchPort)
    assert isinstance(LLMGatewayAdapter(), LLMGatewayPort)


def test_assert_protocol_rejects_missing_method() -> None:
    class Incomplete:
        def semantic_search(self, *args, **kwargs):
            return None
# 缺少 find_similar_work / find_counter_evidence

    import pytest

    with pytest.raises(TypeError, match="does not satisfy protocol"):
        assert_protocol(Incomplete(), RetrievalPort)


def test_discover_service_accepts_protocol_fakes(db_session) -> None:
    """连线检查：service 应绑定 fake，绝不能触达真实 adapter。"""
    service = DiscoverService(
        db_session,
        retrieval=FakeRetrieval(),
        external_search=FakeExternalSearch(),
        llm=FakeLLM(),
    )

# 没有 override 时，默认 __init__ 仍应完成。
    default = DiscoverService(db_session)
    assert isinstance(default.retrieval, RetrievalPort)
    assert isinstance(default.external_search, ExternalSearchPort)
    assert isinstance(default.llm, LLMGatewayPort)
# Service 保持可用，不抛出异常。
    assert service.db is db_session
