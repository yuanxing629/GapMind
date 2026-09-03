"""Cross-domain ports for the Discover service.

The Discover pipeline coordinates retrieval (workspace + external), an
external-paper search client, and an LLM gateway. Without these ports,
``DiscoverService`` would import concrete modules from three other
domains — making it hard to:

  * unit-test the orchestration without bringing up Milvus + an LLM;
  * swap the LLM or the external-search provider (the next chat-agent
    iteration may need LangChain / Anthropic / OpenAI alongside the remote
    Chat Completions provider);
  * trace exactly which call path a given Discover run took.

A ``Port`` here is a ``typing.Protocol`` describing the surface that
``DiscoverService`` depends on. ``adapters.py`` ships the production
implementations; tests can pass any object that satisfies the protocol.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.domains.retrieval.schemas import RetrievalResponse


@runtime_checkable
class RetrievalPort(Protocol):
    """Subset of the retrieval service used by Discover."""

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
    """Search-external-candidates client (Semantic Scholar for now)."""

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
    """Minimal LLM surface — the Discover pipeline only calls chat completion."""

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
