"""Judgement Gateway - LLM-based NLI for counter-evidence detection.

Uses the configured OpenAI Chat Completions-compatible provider to classify the
relationship between a claim and retrieved passages: supports / overlaps /
qualifies / contradicts / unknown.

Required by Contract D: counter_evidence results must pass through
rerank or LLM/NLI judgement before being returned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from openai import OpenAI

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# Valid judgement values per Contract D
VALID_JUDGEMENTS = {"supports", "overlaps", "qualifies", "contradicts", "unknown"}

SYSTEM_PROMPT = """\
You are a research evidence classifier. Given a CLAIM and a list of PASSAGES, \
classify the relationship of each passage to the claim.

Judgement categories:
- supports: passage provides evidence that the claim is true
- contradicts: passage provides evidence that the claim is false or incorrect
- qualifies: passage partially supports but adds important caveats or limitations
- overlaps: passage discusses the same topic but neither supports nor contradicts
- unknown: cannot determine relationship from the passage alone

Rules:
- Be conservative: use "unknown" if evidence is ambiguous
- "contradicts" requires clear opposing evidence, not just different focus
- Consider only what the passage explicitly states or strongly implies

Output a JSON array with one object per passage, in order:
[{"index": 0, "judgement": "...", "confidence": 0.0-1.0}, ...]

Output ONLY the JSON array, no explanation."""


@dataclass
class JudgementHit:
    """Judgement result for a single passage."""

    index: int
    judgement: str = "unknown"
    confidence: float = 0.0


@dataclass
class JudgementResult:
    """Batch judgement response."""

    hits: list[JudgementHit] = field(default_factory=list)
    model: str = ""
    latency_ms: float = 0.0
    error: str | None = None


class JudgementGateway:
    """LLM-based NLI judgement using the configured remote provider.

    Classifies claim-passage relationships for counter-evidence detection.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        backup_api_key: str | None = None,
        backup_base_url: str | None = None,
        backup_model: str | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.remote_api_key
        self.base_url = base_url if base_url is not None else settings.remote_base_url
        self.model = model if model is not None else settings.remote_model
        self.backup_api_key = (
            backup_api_key if backup_api_key is not None else settings.backup_api_key
        )
        self.backup_base_url = (
            backup_base_url if backup_base_url is not None else settings.backup_base_url
        )
        self.backup_model = (
            backup_model if backup_model is not None else settings.backup_model
        )
        self._client: OpenAI | None = None
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
    def backup_enabled(self) -> bool:
        return bool(self.backup_api_key and self.backup_base_url and self.backup_model)

    @property
    def backup_client(self) -> OpenAI:
        if self._backup_client is None:
            self._backup_client = OpenAI(
                api_key=self.backup_api_key,
                base_url=self.backup_base_url,
            )
        return self._backup_client

    def judge_batch(
        self,
        claim: str,
        passages: list[str],
        *,
        max_passages: int = 8,
    ) -> JudgementResult:
        """Judge the relationship between a claim and multiple passages.

        Args:
            claim: The claim text to evaluate.
            passages: List of passage texts (truncated to max_passages).
            max_passages: Maximum passages per LLM call (controls token cost).

        Returns:
            JudgementResult with one JudgementHit per passage.
        """
        import time

        if not passages:
            return JudgementResult(model=self.model)

        # Truncate to control cost
        passages = passages[:max_passages]

        start = time.perf_counter()

        # Build user prompt with numbered passages (truncate each to ~500 chars)
        passage_lines = []
        for i, p in enumerate(passages):
            truncated = p[:500] + ("..." if len(p) > 500 else "")
            passage_lines.append(f"[{i}] {truncated}")

        user_prompt = (
            f"CLAIM: {claim[:300]}\n\n"
            f"PASSAGES:\n" + "\n".join(passage_lines)
        )

        logger.info(
            "judge.request",
            model=self.model,
            claim_len=len(claim),
            passage_count=len(passages),
        )

        try:
            max_tokens = max(1024, len(passages) * 256)
            request = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.0,
                "max_tokens": max_tokens,
            }
            try:
                resp = self.client.chat.completions.create(**request)
            except Exception:
                if not self.backup_enabled:
                    raise
                logger.warning(
                    "judge.fallback",
                    primary_model=self.model,
                    backup_model=self.backup_model,
                )
                request["model"] = self.backup_model
                resp = self.backup_client.chat.completions.create(**request)

            response_model = getattr(resp, "model", None) or request["model"]
            choice = resp.choices[0]
            content = choice.message.content or ""
            if not content.strip():
                raise ValueError(
                    "Judge returned empty content "
                    f"(finish_reason={choice.finish_reason}, "
                    f"max_tokens={max_tokens})"
                )
            latency = (time.perf_counter() - start) * 1000

            hits = self._parse_response(content, len(passages))

            logger.info(
                "judge.response",
                model=response_model,
                hit_count=len(hits),
                finish_reason=choice.finish_reason,
                latency_ms=round(latency, 1),
            )

            return JudgementResult(hits=hits, model=response_model, latency_ms=latency)

        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            logger.error("judge.failed", error=str(e))
            # Graceful degradation: return unknown for all
            hits = [
                JudgementHit(index=i, judgement="unknown", confidence=0.0)
                for i in range(len(passages))
            ]
            return JudgementResult(
                hits=hits, model=self.model, latency_ms=latency, error=str(e)
            )

    def _parse_response(self, content: str, expected_count: int) -> list[JudgementHit]:
        """Parse LLM JSON response into JudgementHit list."""
        # Strip markdown code fences if present
        content = content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        try:
            data = json.loads(content)
            if not isinstance(data, list):
                raise ValueError("Expected JSON array")
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("judge.parse_failed", error=str(e), content=content[:200])
            return [
                JudgementHit(index=i, judgement="unknown", confidence=0.0)
                for i in range(expected_count)
            ]

        hits: list[JudgementHit] = []
        for item in data:
            idx = item.get("index", len(hits))
            judgement = item.get("judgement", "unknown")
            if judgement not in VALID_JUDGEMENTS:
                judgement = "unknown"
            confidence = float(item.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))
            hits.append(JudgementHit(index=idx, judgement=judgement, confidence=confidence))

        # Fill missing indices with unknown
        if len(hits) < expected_count:
            existing_indices = {h.index for h in hits}
            for i in range(expected_count):
                if i not in existing_indices:
                    hits.append(JudgementHit(index=i, judgement="unknown", confidence=0.0))
            hits.sort(key=lambda h: h.index)

        return hits[:expected_count]

    def ping(self) -> bool:
        """Check if API key is configured."""
        return bool(self.api_key and self.base_url and self.model)


_gateway: JudgementGateway | None = None


def get_judgement_gateway() -> JudgementGateway:
    """Singleton accessor for the Judgement gateway."""
    global _gateway
    if _gateway is None:
        _gateway = JudgementGateway()
    return _gateway
