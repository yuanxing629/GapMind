"""Model Gateway：LLM 与 Embedding provider 的统一接口。

Phase 0：仅提供骨架。具体的 LLM 调用将在 Phase 2-3 接入。
"""

from app.gateway.embedding import EmbeddingGateway, get_embedding_gateway
from app.gateway.llm import LLMGateway, get_llm_gateway

__all__ = [
    "LLMGateway",
    "get_llm_gateway",
    "EmbeddingGateway",
    "get_embedding_gateway",
]
