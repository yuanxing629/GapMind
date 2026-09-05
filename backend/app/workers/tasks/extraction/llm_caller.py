"""抽取 worker 使用的 LLM 调用辅助函数。

``app.gateway.llm`` 中的 provider-neutral wrapper 已负责传输；本模块补充
*structured extraction* 特有的逻辑：

* JSON 解析失败时重试（模型偶尔会漏掉括号或忘记闭合 ``]``）；
* 修复 JSON（移除 ```json 围栏、删除末尾逗号）；
* 提供足够空间容纳整篇论文的 ``items + relations`` payload（开发环境观察到约 16 K tokens）。

本模块的函数无状态；重试循环只保存局部状态，LLM gateway 延迟解析，以便测试替换它。
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.core.logging import get_logger
from app.gateway.llm import LLMGateway

logger = get_logger(__name__)

# 输出预算：一篇 30 页论文的“methods + tasks + datasets + claims + limitations +
# relations”抽取结果通常达到 8-12 K 个 JSON token。16 384 可以留出余量，
# 同时不触及 gateway 的 32 K 上限。
DEFAULT_MAX_TOKENS = 16_384
DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2.0
JSON_RESPONSE_FORMAT = {"type": "json_object"}
RETRY_INSTRUCTION = (
    "Your previous response was invalid or incomplete. Return a compact and complete "
    'JSON object only. It must contain the top-level keys "items" and "relations". '
    "Keep the required schema fields, omit optional detail rather than truncating, "
    "and do not include prose, Markdown fences, or any text outside the JSON object."
)


def _response_diagnostics(response: Any) -> dict[str, Any]:
    """从 OpenAI-compatible 响应中提取不会泄漏正文的截断诊断字段。"""
    raw_response = getattr(response, "raw", None)
    choices = getattr(raw_response, "choices", None) or []
    choice = choices[0] if choices else None
    usage = getattr(raw_response, "usage", None)
    return {
        "finish_reason": getattr(choice, "finish_reason", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def call_llm_with_retry(
    messages: list[dict[str, str]],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> tuple[str, dict[str, Any] | None]:
    """调用 LLM 并将响应解析为 JSON，返回 ``(raw, parsed)``。

    当所有尝试都未能生成包含 ``items`` 键的有效 JSON 时，``parsed`` 为 ``None``。
    调用方负责将原始响应持久化为 ``ExtractionRejection`` 以供审计。
    """
    gateway = LLMGateway()
    last_raw = ""
    for attempt in range(max_retries + 1):
        try:
            attempt_messages = messages
            if attempt > 0:
                attempt_messages = [
                    *messages,
                    {"role": "user", "content": RETRY_INSTRUCTION},
                ]
            response = gateway.chat_completion(
                attempt_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=JSON_RESPONSE_FORMAT,
# 结构化抽取：否则 reasoning model 可能将整个预算耗费在 CoT 上并返回空内容
#（见方案 §八）。
                disable_thinking=True,
            )
            raw = response.content
            last_raw = raw
            parsed = parse_llm_json(raw)
            if parsed is not None and "items" in parsed:
                return raw, parsed
            logger.warning(
                "extract_knowledge.parse_retry",
                attempt=attempt,
                raw_preview=raw[:200],
                raw_length=len(raw),
                **_response_diagnostics(response),
            )
        except Exception as exc:  # pragma: no cover — depends on gateway behaviour
            logger.warning("extract_knowledge.llm_error", attempt=attempt, error=str(exc))
        if attempt < max_retries:
            time.sleep(RETRY_BACKOFF_SECONDS)
    return last_raw, None


def parse_llm_json(raw: str) -> dict[str, Any] | None:
    """从 LLM 响应中提取 JSON 对象。

    处理三种常见形式：

    1. ```` ```json\n{...}\n``` ```` 代码围栏
    2. 嵌入 ``{...}`` 的 prose（提取最外层大括号）
    3. 带多余末尾逗号的有效 JSON（先移除末尾逗号）
    """
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
    if match:
        raw = match.group(1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


__all__ = ["call_llm_with_retry", "parse_llm_json"]
