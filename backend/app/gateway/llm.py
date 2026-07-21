"""LLM Gateway - Deepseek integration.

Phase 0: skeleton with a minimal `chat_completion` method. Phase 2-3 will add
structured-output extraction, retry, and token/cost tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openai import OpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class LLMResponse:
    """Normalized LLM response."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    raw: Any = None


class LLMGateway:
    """Thin wrapper over Deepseek's OpenAI-compatible API.

    Uses the `openai` SDK with a custom base_url. Phase 0 only implements the
    basic chat completion; structured extraction and cost tracking come later.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.deepseek_api_key
        self.base_url = base_url if base_url is not None else settings.deepseek_base_url
        self.model = model if model is not None else settings.deepseek_model
        self._client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "DEEPSEEK_API_KEY is not set. Configure backend/.env."
                )
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        """Run a chat completion against the configured Deepseek model."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        logger.info("llm.chat.start", model=self.model, messages=len(messages))
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        usage = resp.usage
        return LLMResponse(
            content=choice.message.content or "",
            model=resp.model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
            total_tokens=getattr(usage, "total_tokens", 0) if usage else 0,
            raw=resp,
        )

    def ping(self) -> bool:
        """Lightweight connectivity check - returns True if API key is set.

        A real network ping is deferred to Phase 2 to avoid spamming the API
        during health checks.
        """
        return bool(self.api_key)


_gateway: LLMGateway | None = None


def get_llm_gateway() -> LLMGateway:
    """Singleton accessor for the LLM gateway."""
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
