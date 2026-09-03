"""LLM Gateway for OpenAI Chat Completions-compatible providers.

Phase 0: skeleton with a minimal `chat_completion` method. Phase 2-3 will add
structured-output extraction, retry, and token/cost tracking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generator

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
    """Thin wrapper over OpenAI Chat Completions-compatible APIs.

    The normal text path uses the remote endpoint. Image requests use the
    separately configured vision endpoint and never fall back to the text-only
    backup endpoint.

    When a backup OpenAI-compatible endpoint is fully configured (key +
    base_url + model), a text request falls over to it once. If the backup also
    fails, the original primary error is raised.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        backup_api_key: str | None = None,
        backup_base_url: str | None = None,
        backup_model: str | None = None,
        vision_api_key: str | None = None,
        vision_base_url: str | None = None,
        vision_model: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.remote_api_key
        self.base_url = base_url if base_url is not None else settings.remote_base_url
        self.model = model if model is not None else settings.remote_model
        self.backup_api_key = (
            backup_api_key if backup_api_key is not None else settings.backup_api_key
        )
        self.backup_base_url = (
            backup_base_url
            if backup_base_url is not None
            else settings.backup_base_url
        )
        self.backup_model = (
            backup_model if backup_model is not None else settings.backup_model
        )
        self.vision_api_key = (
            vision_api_key
            if vision_api_key is not None
            else (settings.vision_api_key or self.api_key)
        )
        self.vision_base_url = (
            vision_base_url
            if vision_base_url is not None
            else (settings.vision_base_url or self.base_url)
        )
        self.vision_model = (
            vision_model if vision_model is not None else settings.vision_model
        )
        self._client: OpenAI | None = None
        self._vision_client: OpenAI | None = None
        self._backup_client: OpenAI | None = None

    @property
    def client(self) -> OpenAI:
        if self._client is None:
            if not self.api_key:
                raise RuntimeError(
                    "REMOTE_API_KEY is not set. Configure the repo-root .env."
                )
            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    @property
    def vision_client(self) -> OpenAI:
        if self._vision_client is None:
            if not self.vision_api_key:
                raise RuntimeError(
                    "VISION_API_KEY or REMOTE_API_KEY is not set. "
                    "Configure the repo-root .env."
                )
            self._vision_client = OpenAI(
                api_key=self.vision_api_key,
                base_url=self.vision_base_url,
            )
        return self._vision_client

    @property
    def backup_enabled(self) -> bool:
        return bool(self.backup_api_key and self.backup_base_url and self.backup_model)

    @property
    def backup_client(self) -> OpenAI:
        if self._backup_client is None:
            self._backup_client = OpenAI(
                api_key=self.backup_api_key, base_url=self.backup_base_url
            )
        return self._backup_client

    def _create_with_fallback(
        self,
        kwargs: dict[str, Any],
        *,
        client: OpenAI,
        stream: bool = False,
        allow_backup: bool = True,
    ) -> Any:
        """Call the primary endpoint, fall over to the backup on failure.

        Streaming can only fail over before the first chunk is yielded; once
        the stream has started, errors propagate to the caller.
        """
        try:
            return client.chat.completions.create(**kwargs, stream=stream)
        except Exception as primary_error:
            backup_attempts: list[dict[str, Any]] = []
            if allow_backup and self.backup_enabled:
                backup_attempts.append({**kwargs, "model": self.backup_model})
            if not backup_attempts:
                raise
            logger.warning(
                "llm.chat.fallback",
                primary_model=self.model,
                backup_model=self.backup_model,
                error=str(primary_error)[:300],
            )
            for attempt_kwargs in backup_attempts:
                try:
                    return self.backup_client.chat.completions.create(
                        **attempt_kwargs, stream=stream
                    )
                except Exception as backup_error:
                    logger.warning(
                        "llm.chat.fallback.failed", error=str(backup_error)[:300]
                    )
            raise primary_error

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        disable_thinking: bool = False,
        model_override: str | None = None,
    ) -> LLMResponse:
        """Run a chat completion against the configured remote model.

        ``disable_thinking`` remains accepted so existing structured callers do
        not need to change, but OpenAI Chat Completions has no provider-neutral
        thinking switch. It is therefore intentionally not serialized as a
        vendor-specific request field.
        """
        request_model = model_override or self.model
        kwargs: dict[str, Any] = {
            "model": request_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        logger.info("llm.chat.start", model=request_model, messages=len(messages))
        client = self.vision_client if model_override is not None else self.client
        resp = self._create_with_fallback(
            kwargs,
            client=client,
            allow_backup=model_override is None,
        )
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


    def stream_chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        disable_thinking: bool = False,
        model_override: str | None = None,
    ) -> Generator[str, None, None]:
        """Yield text deltas from a streaming chat completion (P0.5-1).

        Mirror of ``chat_completion`` but with ``stream=True``; each yielded
        string is one content delta. Structured-format callers should keep
        using the non-streaming version.
        """
        request_model = model_override or self.model
        kwargs: dict[str, Any] = {
            "model": request_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        client = self.vision_client if model_override is not None else self.client
        stream = self._create_with_fallback(
            kwargs,
            client=client,
            stream=True,
            allow_backup=model_override is None,
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    def ping(self) -> bool:
        """Lightweight connectivity check - returns True if API key is set.

        A real network ping is deferred to Phase 2 to avoid spamming the API
        during health checks.
        """
        return bool(self.api_key and self.base_url and self.model)


_gateway: LLMGateway | None = None


def get_llm_gateway() -> LLMGateway:
    """Singleton accessor for the LLM gateway."""
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
