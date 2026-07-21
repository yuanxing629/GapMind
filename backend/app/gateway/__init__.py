"""Model Gateway - unified interface for LLM and Embedding providers.

Phase 0: skeleton only. Concrete LLM calls will be wired in Phase 2-3.
"""

from app.gateway.embedding import EmbeddingGateway, get_embedding_gateway
from app.gateway.llm import LLMGateway, get_llm_gateway

__all__ = [
    "LLMGateway",
    "get_llm_gateway",
    "EmbeddingGateway",
    "get_embedding_gateway",
]
