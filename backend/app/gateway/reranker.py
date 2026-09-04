"""Reranker Gateway：SiliconFlow BGE-reranker 集成。

为检索候选提供 cross-encoder reranking。使用 SiliconFlow 的 /v1/rerank endpoint
（与 embedding 使用同一 API key）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class RerankHit:
    """单条重排序结果。"""

    index: int  # 输入文档列表中的原始索引
    relevance_score: float


@dataclass
class RerankResult:
    """规范化的重排序响应。"""

    hits: list[RerankHit] = field(default_factory=list)
    model: str = ""
    latency_ms: float = 0.0


class RerankerGateway:
    """SiliconFlow rerank endpoint 的包装器。

    Model：BAAI/bge-reranker-v2-m3（cross-encoder、multilingual）。
    Endpoint：POST {base_url}/rerank
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.siliconflow_api_key
        # Rerank endpoint 与 embedding 使用同一个 base URL。
        self.base_url = (base_url if base_url is not None else settings.siliconflow_base_url).rstrip("/")
        self.model = model if model is not None else settings.reranker_model
        self.timeout = timeout

    def rerank(
        self,
        query: str,
        documents: list[str],
        *,
        top_n: int | None = None,
    ) -> RerankResult:
        """按文档与 query 的相关性重排序。

        参数：
            query：搜索 query 或 claim 文本。
            documents：待 rerank 的段落文本列表。
            top_n：仅返回前 N 个结果（默认全部返回）。

        返回：
            返回按 relevance_score 降序排列命中的 RerankResult。
        """
        if not documents:
            return RerankResult(model=self.model)

        if not self.api_key:
            raise RuntimeError(
                "SILICONFLOW_API_KEY is not set. Configure the repo-root .env."
            )

        import time

        start = time.perf_counter()

        payload: dict = {
            "model": self.model,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            payload["top_n"] = top_n

        url = f"{self.base_url}/rerank"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.info(
            "reranker.request",
            model=self.model,
            query_len=len(query),
            doc_count=len(documents),
            top_n=top_n,
        )

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        latency = (time.perf_counter() - start) * 1000

        hits = [
            RerankHit(
                index=item["index"],
                relevance_score=item["relevance_score"],
            )
            for item in data.get("results", [])
        ]
        # 按相关性降序排序
        hits.sort(key=lambda h: h.relevance_score, reverse=True)

        logger.info(
            "reranker.response",
            model=self.model,
            hit_count=len(hits),
            latency_ms=round(latency, 1),
        )

        return RerankResult(hits=hits, model=self.model, latency_ms=latency)

    def ping(self) -> bool:
        """检查是否配置了 API key。"""
        return bool(self.api_key)


_gateway: RerankerGateway | None = None


def get_reranker_gateway() -> RerankerGateway:
    """Reranker gateway 的单例访问器。"""
    global _gateway
    if _gateway is None:
        _gateway = RerankerGateway()
    return _gateway
