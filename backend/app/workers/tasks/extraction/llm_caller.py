"""LLM-call helpers used by the extraction worker.

The provider-neutral wrapper in ``app.gateway.llm`` already handles transport;
this module adds the things specific to *structured extraction*:

  * retry on JSON-parse failure (the model occasionally drops a brace or
    forgets the closing ``]``);
  * JSON repair (strip ```json fences, drop trailing commas);
  * a ``max_tokens`` ceiling large enough to fit a full paper's
    ``items + relations`` payload (≈16 K tokens observed in dev).

The functions here are stateless — the retry loop captures only the
local state, the LLM gateway is resolved lazily so tests can stub it.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.core.logging import get_logger
from app.gateway.llm import LLMGateway

logger = get_logger(__name__)

# Output budget: an extraction of "methods + tasks + datasets + claims +
# limitations + relations" for a 30-page paper routinely hits 8-12 K
# tokens of JSON. 16 384 leaves headroom without bumping into the
# gateway's 32 K ceiling.
DEFAULT_MAX_TOKENS = 16_384
DEFAULT_TEMPERATURE = 0.1
DEFAULT_MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 2.0


def call_llm_with_retry(
    messages: list[dict[str, str]],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> tuple[str, dict[str, Any] | None]:
    """Call the LLM and parse the response as JSON. Returns ``(raw, parsed)``.

    ``parsed`` is ``None`` when every attempt failed to produce valid JSON
    with an ``items`` key. The caller is responsible for persisting the
    raw response as an ``ExtractionRejection`` for audit.
    """
    gateway = LLMGateway()
    last_raw = ""
    for attempt in range(max_retries + 1):
        try:
            response = gateway.chat_completion(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                # Structured extraction: the reasoning model otherwise burns the
                # whole budget on CoT and returns empty content (see plan §八).
                disable_thinking=True,
            )
            raw = response.content
            last_raw = raw
            parsed = parse_llm_json(raw)
            if parsed is not None and "items" in parsed:
                return raw, parsed
            logger.warning("extract_knowledge.parse_retry", attempt=attempt, raw_preview=raw[:200])
        except Exception as exc:  # pragma: no cover — depends on gateway behaviour
            logger.warning("extract_knowledge.llm_error", attempt=attempt, error=str(exc))
        if attempt < max_retries:
            time.sleep(RETRY_BACKOFF_SECONDS)
    return last_raw, None


def parse_llm_json(raw: str) -> dict[str, Any] | None:
    """Pull a JSON object out of an LLM response.

    Handles three common shapes:

    1. ```` ```json\n{...}\n``` ```` fenced block
    2. prose with an embedded ``{...}`` (we grab the outermost braces)
    3. valid JSON with a stray trailing comma (we strip it)
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
