"""Semantic Scholar Academic Graph API client.

The API key is intentionally kept on the backend. The frontend talks to our
own API and never receives the Semantic Scholar credential.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.core.semantic_scholar_control import (
    read_search_cache,
    search_cache_key,
    wait_for_request_slot,
    write_search_cache,
)


RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
PDF_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


def semantic_scholar_failure_kind(
    status_code: int | None = None,
    *,
    timeout: bool = False,
    transport: bool = False,
) -> str:
    """Return a stable category for an upstream failure.

    Raw exception text remains available for diagnostics, while this category
    is safe to use in product summaries and recovery decisions.
    """
    if timeout or status_code == 504:
        return "timeout"
    if status_code == 429:
        return "rate_limited"
    if transport or status_code == 502:
        return "network_error"
    if status_code is not None and status_code >= 500:
        return "upstream_error"
    return "request_error"


class SemanticScholarError(Exception):
    """An error returned by, or raised while calling, Semantic Scholar."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        failure_kind: str | None = None,
        attempts: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.failure_kind = failure_kind or semantic_scholar_failure_kind(status_code)
        self.attempts = attempts


class SemanticScholarClient:
    """Small synchronous client for the Academic Graph paper endpoints."""

    def __init__(self, timeout: float = 20.0) -> None:
        self.base_url = settings.semantic_scholar_base_url.rstrip("/") + "/"
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        # API keys are optional for Semantic Scholar, but recommended. Do not
        # send an empty header when local development has no key configured.
        if settings.semantic_scholar_api_key:
            return {"x-api-key": settings.semantic_scholar_api_key}
        return {}

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        response: httpx.Response | None = None
        retry_count = max(0, settings.semantic_scholar_retry_count)
        for attempt in range(retry_count + 1):
            try:
                wait_for_request_slot()
                with httpx.Client(
                    base_url=self.base_url,
                    headers=self._headers(),
                    timeout=self.timeout,
                ) as client:
                    response = client.get(path.lstrip("/"), params=params)
            except httpx.TimeoutException as exc:
                if attempt < retry_count:
                    time.sleep(self._retry_delay(attempt, None))
                    continue
                raise SemanticScholarError(
                    "Semantic Scholar request timed out",
                    status_code=504,
                    failure_kind="timeout",
                    attempts=attempt + 1,
                ) from exc
            except httpx.RequestError as exc:
                if attempt < retry_count:
                    time.sleep(self._retry_delay(attempt, None))
                    continue
                raise SemanticScholarError(
                    f"Semantic Scholar request failed: {exc}",
                    status_code=502,
                    failure_kind="network_error",
                    attempts=attempt + 1,
                ) from exc

            if response.status_code not in RETRYABLE_STATUS_CODES or attempt >= retry_count:
                break
            time.sleep(self._retry_delay(attempt, response.headers.get("Retry-After")))

        if response is None:
            raise SemanticScholarError(
                "Semantic Scholar returned no response",
                status_code=502,
                failure_kind="network_error",
                attempts=retry_count + 1,
            )

        if response.is_error:
            message = response.text[:500] or response.reason_phrase
            try:
                body = response.json()
                if isinstance(body, dict) and body.get("message"):
                    message = str(body["message"])
            except ValueError:
                pass
            raise SemanticScholarError(
                message,
                status_code=response.status_code,
                attempts=attempt + 1,
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise SemanticScholarError(
                "Semantic Scholar returned invalid JSON",
                status_code=502,
                failure_kind="upstream_error",
                attempts=attempt + 1,
            ) from exc
        if not isinstance(payload, dict):
            raise SemanticScholarError(
                "Semantic Scholar returned an unexpected response",
                status_code=502,
                failure_kind="upstream_error",
                attempts=attempt + 1,
            )
        return payload

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None) -> float:
        """Calculate a bounded delay for a retry attempt."""
        try:
            retry_after_seconds = float(retry_after) if retry_after else 0.0
        except ValueError:
            retry_after_seconds = 0.0
        exponential = max(0.0, settings.semantic_scholar_retry_backoff) * (2**attempt)
        # Do not let a malformed or unexpectedly large upstream header block
        # a worker indefinitely. The shared request slot protects the retry.
        return min(30.0, max(retry_after_seconds, exponential))

    def search(
        self,
        *,
        query: str,
        fields: str,
        sort: str,
        limit: int,
        offset: int = 0,
        token: str | None = None,
        **filters: Any,
    ) -> dict[str, Any]:
        """Search papers using relevance search or bulk sorted search."""

        is_relevance = sort == "relevance"
        path = "paper/search" if is_relevance else "paper/search/bulk"
        params: dict[str, Any] = {
            "query": query,
            "fields": fields,
            "limit": limit,
        }
        params.update({key: value for key, value in filters.items() if value is not None})

        if is_relevance:
            params["offset"] = offset
        else:
            params["sort"] = sort
            if token:
                params["token"] = token

        cache_key = search_cache_key(params)
        cached = read_search_cache(cache_key)
        if cached is not None:
            return cached
        payload = self._get(path, params)
        write_search_cache(cache_key, payload)
        return payload

    def download_pdf(self, url: str, *, max_bytes: int = 50 * 1024 * 1024) -> bytes:
        """Download and validate an open-access PDF.

        OA URLs often point to a publisher or repository rather than
        Semantic Scholar itself. Retry only transient failures, preserve the
        actual HTTP status for diagnosis, and never treat an HTML landing page
        as a PDF.
        """
        if not url.lower().startswith("https://"):
            raise SemanticScholarError(
                "Open-access PDF URL must use HTTPS",
                status_code=400,
                failure_kind="request_error",
            )
        retry_count = min(2, max(0, settings.semantic_scholar_retry_count))
        for attempt in range(retry_count + 1):
            retry_after_hint: str | None = None
            try:
                with httpx.Client(
                    timeout=httpx.Timeout(30.0, connect=10.0),
                    follow_redirects=True,
                    headers={"User-Agent": "GapMind/1.0"},
                ) as client:
                    with client.stream("GET", url) as response:
                        retry_after_hint = response.headers.get("Retry-After")
                        if response.is_error:
                            status_code = response.status_code
                            raise SemanticScholarError(
                                f"Open-access PDF download failed: HTTP {status_code}",
                                status_code=status_code,
                                failure_kind=semantic_scholar_failure_kind(status_code),
                            )
                        content_length = response.headers.get("Content-Length")
                        if content_length:
                            try:
                                if int(content_length) > max_bytes:
                                    raise SemanticScholarError(
                                        "Open-access PDF is too large", status_code=413
                                    )
                            except ValueError as exc:
                                raise SemanticScholarError(
                                    "Open-access PDF returned an invalid size",
                                    status_code=502,
                                    failure_kind="request_error",
                                ) from exc
                        chunks: list[bytes] = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > max_bytes:
                                raise SemanticScholarError(
                                    "Open-access PDF is too large", status_code=413
                                )
                            chunks.append(chunk)
                        content = b"".join(chunks)
            except SemanticScholarError as exc:
                if exc.status_code in PDF_RETRYABLE_STATUS_CODES and attempt < retry_count:
                    time.sleep(self._retry_delay(attempt, retry_after_hint))
                    continue
                exc.attempts = attempt + 1
                raise
            except httpx.TimeoutException as exc:
                if attempt < retry_count:
                    time.sleep(self._retry_delay(attempt, None))
                    continue
                raise SemanticScholarError(
                    "Open-access PDF download timed out",
                    status_code=504,
                    failure_kind="timeout",
                    attempts=attempt + 1,
                ) from exc
            except httpx.RequestError as exc:
                if attempt < retry_count:
                    time.sleep(self._retry_delay(attempt, None))
                    continue
                raise SemanticScholarError(
                    f"Open-access PDF download failed: {exc}",
                    status_code=502,
                    failure_kind="network_error",
                    attempts=attempt + 1,
                ) from exc

            if not content.startswith(b"%PDF"):
                raise SemanticScholarError(
                    "Downloaded open-access file is not a PDF",
                    status_code=422,
                    failure_kind="invalid_pdf",
                    attempts=attempt + 1,
                )
            return content
        raise SemanticScholarError(
            "Open-access PDF download failed",
            status_code=502,
            failure_kind="network_error",
            attempts=retry_count + 1,
        )

    def get_paper(self, paper_id: str, *, fields: str) -> dict[str, Any]:
        """Fetch one paper by Semantic Scholar, DOI, arXiv, or Corpus ID."""

        encoded_id = quote(paper_id, safe=":")
        return self._get(f"paper/{encoded_id}", {"fields": fields})
