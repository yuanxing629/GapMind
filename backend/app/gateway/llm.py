"""兼容 OpenAI Chat Completions 的 LLM Gateway。

Phase 0：提供最小 `chat_completion` 方法的骨架。Phase 2-3 将增加结构化输出抽取、重试
以及 token/cost 跟踪。
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
    """规范化的 LLM 响应。"""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    raw: Any = None


class LLMGateway:
    """OpenAI Chat Completions 兼容 API 的轻量包装器。

    普通文本路径使用远程 endpoint。图像请求使用单独配置的 vision endpoint，绝不回退到
    仅支持文本的 backup endpoint。

    当备用 OpenAI-compatible endpoint 已完整配置（key + base_url + model）时，文本请求
    失败后会回退一次。如果备用 endpoint 也失败，则抛出原始 primary error。
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
        """调用主端点，失败时回退到备用端点。

        Streaming 只能在产出第一个 chunk 之前执行 failover；流开始后，错误会直接传递给
        调用方。
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
        """使用配置的远程模型执行 chat completion。

        保留接受 ``disable_thinking``，使既有结构化调用方无需修改；但 OpenAI Chat
        Completions 没有 provider-neutral 的 thinking 开关，因此不会将其序列化为
        厂商特定的 request field。
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
        """从流式 chat completion 产出文本增量（P0.5-1）。

        这是 ``chat_completion`` 的流式版本，使用 ``stream=True``；每次 yield 一个
        content delta。结构化格式的调用方应继续使用非流式版本。
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
        """轻量连通性检查：已设置 API key 时返回 True。

        真实网络 ping 延后到 Phase 2，以避免 health check 期间频繁请求 API。
        """
        return bool(self.api_key and self.base_url and self.model)


_gateway: LLMGateway | None = None


def get_llm_gateway() -> LLMGateway:
    """LLM gateway 的单例访问器。"""
    global _gateway
    if _gateway is None:
        _gateway = LLMGateway()
    return _gateway
