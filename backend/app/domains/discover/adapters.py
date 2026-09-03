"""Default adapter implementations for the Discover ports.

These are thin wrappers that forward to the production modules
(retrieval service, Semantic Scholar client, LLM gateway). Keeping the
adapters in a separate module lets tests substitute Protocol-compatible
fakes without monkey-patching the underlying libraries.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.discover.ports import ExternalSearchPort, LLMGatewayPort, RetrievalPort
from app.domains.retrieval.schemas import RetrievalResponse
from app.domains.retrieval.service import (
    find_counter_evidence,
    find_similar_work,
    semantic_search,
)
from app.gateway.llm import get_llm_gateway
from app.gateway.semantic_scholar import SemanticScholarClient

logger = get_logger(__name__)


class RetrievalAdapter:
    """Forward Discover's retrieval calls to the retrieval service."""

    def __init__(self, db: Session | None = None) -> None:
        self.db = db

    def _require_db(self) -> Session:
        if self.db is None:
            raise RuntimeError("RetrievalAdapter requires a database session for paper chunks")
        return self.db

    def semantic_search(
        self,
        workspace_id: str,
        query: str,
        top_k: int,
        *,
        use_reranker: bool = True,
        **kwargs: Any,
    ) -> RetrievalResponse:
        return semantic_search(workspace_id, query, top_k, use_reranker=use_reranker)

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
        return find_similar_work(
            workspace_id,
            paper_id,
            top_k,
            db=self._require_db(),
            use_reranker=use_reranker,
            exclude_paper_ids=exclude_paper_ids,
        )

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
        return find_counter_evidence(
            workspace_id,
            claim,
            top_k,
            use_reranker=use_reranker,
            use_judge=use_judge,
            exclude_paper_ids=exclude_paper_ids,
        )


class ExternalSearchAdapter:
    """Forward external-paper search to the Semantic Scholar client."""

    def __init__(self, client: SemanticScholarClient | None = None) -> None:
        self._client = client or SemanticScholarClient()

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
        return self._client.search(
            query=query,
            fields=fields,
            sort=sort,
            limit=limit,
            year=year,
            token=token,
        )

    def get_paper(self, paper_id: str, *, fields: str) -> dict[str, Any]:
        return self._client.get_paper(paper_id, fields=fields)


class LLMGatewayAdapter:
    """Forward LLM calls to the configured gateway."""

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> Any:
        return get_llm_gateway().chat_completion(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )


__all__ = ["RetrievalAdapter", "ExternalSearchAdapter", "LLMGatewayAdapter"]


def assert_protocol(port: object, expected: type) -> None:
    """Sanity-check a port binding at startup.

    ``runtime_checkable`` lets us do this cheaply — failing here beats
    waiting for a runtime AttributeError three commits from now.
    """
    if not isinstance(port, expected):
        raise TypeError(
            f"Port {port!r} does not satisfy protocol {expected.__name__}. "
            "Check that the adapter exposes every method the protocol declares."
        )
