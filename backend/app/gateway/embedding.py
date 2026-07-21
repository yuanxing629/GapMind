"""Embedding Gateway - SiliconFlow BGE-m3 integration.

Phase 0: skeleton with a basic `embed_texts` method. Phase 2 will add
batching, Milvus integration, and embedding version tracking.
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
    """Normalized embedding response."""

    embeddings: list[list[float]]
    model: str
    dim: int
    total_tokens: int = 0
    raw: Any = None


class EmbeddingGateway:
    """Wrapper over SiliconFlow's OpenAI-compatible embedding endpoint.

    Uses BGE-m3 (1024-dim, 8192 context) for paper/academic text.
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
                    "SILICONFLOW_API_KEY is not set. Configure backend/.env."
                )
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def embed_texts(
        self,
        texts: list[str],
        *,
        batch_size: int = 16,
    ) -> EmbeddingResult:
        """Embed a list of texts. Batches to stay under provider limits."""
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
        """Convenience: embed a single text, return its vector."""
        return self.embed_texts([text]).embeddings[0]

    def ping(self) -> bool:
        """Lightweight check - returns True if API key is configured."""
        return bool(self.api_key)


_gateway: EmbeddingGateway | None = None


def get_embedding_gateway() -> EmbeddingGateway:
    """Singleton accessor for the Embedding gateway."""
    global _gateway
    if _gateway is None:
        _gateway = EmbeddingGateway()
    return _gateway
