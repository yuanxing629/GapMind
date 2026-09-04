"""Embedding Gateway：SiliconFlow BGE-m3 集成。

Phase 0：提供基础 `embed_texts` 方法的骨架。Phase 2 将增加 batching、Milvus 集成和
embedding 版本跟踪。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class EmbeddingResult:
    """规范化的 embedding 响应。"""

    embeddings: list[list[float]]
    model: str
    dim: int
    total_tokens: int = 0
    raw: Any = None


class EmbeddingGateway:
    """SiliconFlow OpenAI-compatible embedding endpoint 的包装器。

    使用 BGE-m3（1024 维、8192 context）处理论文和学术文本。
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        dim: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.siliconflow_api_key
        self.base_url = base_url if base_url is not None else settings.siliconflow_base_url
        self.model = model if model is not None else settings.embedding_model
        self.dim = dim if dim is not None else settings.embedding_dimension
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "SILICONFLOW_API_KEY is not set. Configure the repo-root .env."
                )
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def embed_texts(
        self,
        texts: list[str],
        *,
        batch_size: int = 16,
    ) -> EmbeddingResult:
        """将文本列表向量化，并分批处理以遵守 provider 限制。"""
        all_embeddings: list[list[float]] = []
        total_tokens = 0

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            logger.info(
                "embedding.batch.start",
                model=self.model,
                batch_index=i // batch_size,
                batch_size=len(batch),
            )
            resp = self.client.embeddings.create(model=self.model, input=batch)
            for item in resp.data:
                all_embeddings.append(item.embedding)
            usage = getattr(resp, "usage", None)
            if usage is not None:
                total_tokens += getattr(usage, "total_tokens", 0)

        return EmbeddingResult(
            embeddings=all_embeddings,
            model=self.model,
            dim=self.dim,
            total_tokens=total_tokens,
        )

    def embed_one(self, text: str) -> list[float]:
        """便捷方法：将单条文本向量化并返回其向量。"""
        return self.embed_texts([text]).embeddings[0]

    def ping(self) -> bool:
        """轻量检查：已配置 API key 时返回 True。"""
        return bool(self.api_key)


_gateway: EmbeddingGateway | None = None


def get_embedding_gateway() -> EmbeddingGateway:
    """Embedding gateway 的单例访问器。"""
    global _gateway
    if _gateway is None:
        _gateway = EmbeddingGateway()
    return _gateway
