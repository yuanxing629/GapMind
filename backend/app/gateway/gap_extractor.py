"""Ollama adapter dedicated to the fine-tuned Schema 3.0 extractor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.domains.gap.prompt import TRAINING_INSTRUCTION, repair_prompt
from app.domains.gap.schemas import GapAnnotationOutput
from app.domains.gap.validation import (
    categorize_validation_errors,
    parse_model_json,
    validate_annotation,
)

logger = get_logger(__name__)


@dataclass
class GapExtractionResult:
    output: GapAnnotationOutput | None
    attempts: int
    raw_responses: list[str] = field(default_factory=list)
    api_responses: list[dict[str, Any]] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)
    provider: str = "ollama"
    model: str = ""
    validation_error_categories: list[str] = field(default_factory=list)


class GapExtractor(Protocol):
    """Provider-neutral contract for the local extractor and its backup."""

    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    @property
    def model_parameters(self) -> dict[str, Any]: ...

    def extract(
        self,
        markdown: str,
        *,
        instruction: str = TRAINING_INSTRUCTION,
        repair_attempts: int | None = None,
    ) -> GapExtractionResult: ...


class GapExtractorUnavailableError(RuntimeError):
    """A safe, actionable failure when the tunneled Ollama service is unavailable."""


class OllamaGapExtractor:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = (base_url or settings.gap_extractor_base_url).rstrip("/")
        self.model = model or settings.gap_extractor_model
        self.client = client or httpx.Client(timeout=settings.gap_extractor_timeout_seconds)

    @property
    def provider(self) -> str:
        return "ollama"

    @property
    def model_parameters(self) -> dict[str, Any]:
        return {
            "num_ctx": settings.gap_extractor_num_ctx,
            "num_predict": settings.gap_extractor_num_predict,
            "temperature": settings.gap_extractor_temperature,
            "top_p": settings.gap_extractor_top_p,
            "repeat_penalty": settings.gap_extractor_repeat_penalty,
            "seed": settings.gap_extractor_seed,
        }

    def _call(self, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        try:
            response = self.client.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "think": False,
                    "messages": messages,
                    "options": self.model_parameters,
                },
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise GapExtractorUnavailableError(
                "研究空白模型响应超时，请检查 SSH 隧道和服务器模型负载后重试。"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                message = "研究空白模型未就绪，请检查 SSH 隧道指向的 Ollama 服务及模型名称。"
            else:
                message = "研究空白模型服务返回异常，请检查 SSH 隧道和服务器 Ollama 服务后重试。"
            raise GapExtractorUnavailableError(message) from exc
        except httpx.RequestError as exc:
            raise GapExtractorUnavailableError(
                "无法连接研究空白模型，请确认 SSH 隧道已连接，并确保本机未启动 Ollama 占用 127.0.0.1:11434。"
            ) from exc
        payload = response.json()
        content = str((payload.get("message") or {}).get("content") or "")
        if not content.strip():
            raise RuntimeError("Ollama returned an empty assistant message")
        return content, payload

    def extract(
        self,
        markdown: str,
        *,
        instruction: str = TRAINING_INSTRUCTION,
        repair_attempts: int | None = None,
    ) -> GapExtractionResult:
        maximum_repairs = (
            settings.gap_extractor_repair_attempts
            if repair_attempts is None
            else max(0, repair_attempts)
        )
        messages = [{"role": "user", "content": f"{instruction.strip()}\n\n{markdown.strip()}"}]
        raw_responses: list[str] = []
        api_responses: list[dict[str, Any]] = []
        errors: list[str] = []

        for attempt in range(1, maximum_repairs + 2):
            raw, api_response = self._call(messages)
            raw_responses.append(raw)
            api_responses.append(api_response)
            try:
                parsed = parse_model_json(raw)
                output, errors = validate_annotation(parsed)
            except ValueError as exc:
                output = None
                errors = [str(exc)]
            if output is not None:
                return GapExtractionResult(
                    output,
                    attempt,
                    raw_responses,
                    api_responses,
                    [],
                    self.provider,
                    self.model,
                    [],
                )
            if attempt <= maximum_repairs:
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": repair_prompt(errors)},
                    ]
                )
        return GapExtractionResult(
            None,
            len(raw_responses),
            raw_responses,
            api_responses,
            errors,
            self.provider,
            self.model,
            categorize_validation_errors(errors),
        )


class RemoteGapExtractor:
    """Explicitly enabled OpenAI-compatible structured-output backup.

    The worker is responsible for checking the server-side feature flag and
    complete remote configuration before constructing this adapter. The adapter
    itself uses the shared LLM gateway so structured calls always pass
    ``disable_thinking=True`` and never use ``reasoning_effort``.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        gateway: Any | None = None,
    ) -> None:
        from app.gateway.llm import LLMGateway

        self.api_key = api_key if api_key is not None else settings.gap_extractor_remote_api_key
        self.base_url = base_url if base_url is not None else settings.gap_extractor_remote_base_url
        self.model = model if model is not None else settings.gap_extractor_remote_model
        self._gateway = gateway or LLMGateway(
            api_key=self.api_key,
            base_url=self.base_url,
            model=self.model,
            backup_api_key="",
            backup_base_url="",
            backup_model="",
        )

    @property
    def provider(self) -> str:
        return "remote"

    @property
    def model_parameters(self) -> dict[str, Any]:
        return {
            # The remote extractor uses Chat Completions JSON mode. Schema 3.0
            # is still enforced locally by validate_annotation below.
            "response_format": "json_object",
            "temperature": 0.0,
            "max_tokens": settings.gap_extractor_remote_max_tokens,
            "disable_thinking": True,
        }

    def extract(
        self,
        markdown: str,
        *,
        instruction: str = TRAINING_INSTRUCTION,
        repair_attempts: int | None = None,
    ) -> GapExtractionResult:
        maximum_repairs = (
            settings.gap_extractor_repair_attempts
            if repair_attempts is None
            else max(0, repair_attempts)
        )
        messages = [{"role": "user", "content": f"{instruction.strip()}\n\n{markdown.strip()}"}]
        raw_responses: list[str] = []
        errors: list[str] = []
        response_format = {"type": "json_object"}

        for attempt in range(1, maximum_repairs + 2):
            try:
                response = self._gateway.chat_completion(
                    messages,
                    temperature=0.0,
                    max_tokens=settings.gap_extractor_remote_max_tokens,
                    response_format=response_format,
                    disable_thinking=True,
                )
            except Exception as exc:
                logger.warning(
                    "gap_extractor.remote_request_failed",
                    model=self.model,
                    base_url=self.base_url,
                    attempt=attempt,
                    error=str(exc)[:500],
                )
                raise GapExtractorUnavailableError(
                    "远程研究空白备份模型不可用，请检查远程 API 配置和服务状态后重试。"
                ) from exc

            raw = response.content
            raw_responses.append(raw)
            try:
                parsed = parse_model_json(raw)
                output, errors = validate_annotation(parsed)
            except ValueError as exc:
                output = None
                errors = [str(exc)]

            if output is not None:
                return GapExtractionResult(
                    output,
                    attempt,
                    raw_responses,
                    [],
                    [],
                    self.provider,
                    self.model,
                    [],
                )

            if attempt <= maximum_repairs:
                messages.extend(
                    [
                        {"role": "assistant", "content": raw},
                        {"role": "user", "content": repair_prompt(errors)},
                    ]
                )

        return GapExtractionResult(
            None,
            len(raw_responses),
            raw_responses,
            [],
            errors,
            self.provider,
            self.model,
            categorize_validation_errors(errors),
        )

