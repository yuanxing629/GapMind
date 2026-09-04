"""Discover service 的跨 domain ports。

Discover pipeline 需要协调检索（workspace + external）、外部论文搜索客户端和 LLM gateway。
如果没有这些 port，``DiscoverService`` 就必须直接导入另外三个 domain 的具体模块，导致：

* 无需启动 Milvus + LLM 即可对编排流程做单元测试；
* 可替换 LLM 或外部搜索 provider（后续 chat-agent iteration 可能需要在远程
  Chat Completions provider 之外接入 LangChain / Anthropic / OpenAI）；
* 精确追踪某次 Discover run 经过的调用路径。

这里的 ``Port`` 是描述 ``DiscoverService`` 所依赖接口面的 ``typing.Protocol``。
``adapters.py`` 提供生产实现；测试可以传入任何满足该 protocol 的对象。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.domains.retrieval.schemas import RetrievalResponse


@runtime_checkable
class RetrievalPort(Protocol):
    """Discover 使用的 retrieval service 子集。"""

    def semantic_search(
        self,
        workspace_id: str,
        query: str,
        top_k: int,
        *,
        use_reranker: bool = True,
        **kwargs: Any,
    ) -> RetrievalResponse:
        ...

    def find_similar_work(
        self,
        workspace_id: str,
        paper_id: str,
        top_k: int,
        *,
        use_reranker: bool = True,
        exclude_paper_ids: set[str] | None = None,
        **kwargs: Any,
    ) -> RetrievalResponse:
        ...

    def find_counter_evidence(
        self,
        workspace_id: str,
        claim: str,
        top_k: int,
        *,
        use_reranker: bool = True,
        use_judge: bool = True,
        exclude_paper_ids: set[str] | None = None,
        **kwargs: Any,
    ) -> RetrievalResponse:
        ...


@runtime_checkable
class ExternalSearchPort(Protocol):
    """外部候选搜索客户端（当前使用 Semantic Scholar）。"""

    def search(
        self,
        query: str,
        *,
        fields: str,
        sort: str = "relevance",
        limit: int = 20,
        year: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        ...

    def get_paper(self, paper_id: str, *, fields: str) -> dict[str, Any]:
        ...


@runtime_checkable
class LLMGatewayPort(Protocol):
    """最小 LLM 接口——Discover 流程只调用 chat completion。"""

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        ...


__all__ = ["RetrievalPort", "ExternalSearchPort", "LLMGatewayPort"]
