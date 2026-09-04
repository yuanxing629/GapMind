"""Semantic Scholar Academic Graph API 客户端。

API key 有意保存在后端。前端只访问我们的 API，永远不会收到 Semantic Scholar 凭据。
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
    """为上游失败返回稳定的分类。

    原始异常文本仍可用于诊断，但该分类可安全用于产品摘要和恢复决策。
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
    """Semantic Scholar 返回或调用过程中抛出的错误。"""

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
    """Academic Graph 论文端点的轻量同步客户端。"""

    def __init__(self, timeout: float = 20.0) -> None:
        self.base_url = settings.semantic_scholar_base_url.rstrip("/") + "/"
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        # Semantic Scholar 的 API key 可选但推荐配置。本地开发未配置 key 时，
        # 不要发送空请求头。
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
        """计算重试尝试的有界等待时间。"""
        try:
            retry_after_seconds = float(retry_after) if retry_after else 0.0
        except ValueError:
            retry_after_seconds = 0.0
        exponential = max(0.0, settings.semantic_scholar_retry_backoff) * (2**attempt)
        # 不要让格式错误或异常大的上游请求头使 worker 无限阻塞。
        # 共享请求槽会保护重试过程。
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
        """使用 relevance 搜索或批量排序搜索论文。"""

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
        """下载并校验开放获取 PDF。

        OA URL 通常指向 publisher 或 repository，而不是 Semantic Scholar 本身。只重试
        transient failure，保留实际 HTTP 状态用于诊断，并且绝不将 HTML landing page 当作 PDF。
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
        """通过 Semantic Scholar、DOI、arXiv 或 Corpus ID 获取一篇论文。"""

        encoded_id = quote(paper_id, safe=":")
        return self._get(f"paper/{encoded_id}", {"fields": fields})
