"""Discover ports 的默认 adapter 实现。

这些是转发到生产模块（检索服务、Semantic Scholar 客户端、LLM gateway）的薄封装。
将 adapter 单独放在本模块，使测试可以传入兼容 Protocol 的 fake，而无需对底层库做
monkey-patch。
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
    """将 Discover 的 retrieval 调用转发给 retrieval service。"""

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
    """将外部论文搜索转发给 Semantic Scholar 客户端。"""

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
    """将 LLM 调用转发给配置的 gateway。"""

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
    """在启动时执行 port binding 的基本检查。

    ``runtime_checkable`` 允许我们低成本完成检查；在这里失败比运行几轮后才遇到
    AttributeError 更容易定位。
    """
    if not isinstance(port, expected):
        raise TypeError(
            f"Port {port!r} does not satisfy protocol {expected.__name__}. "
            "Check that the adapter exposes every method the protocol declares."
        )
