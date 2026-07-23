"""Semantic Scholar Academic Graph API client.

The API key is intentionally kept on the backend. The frontend talks to our
own API and never receives the Semantic Scholar credential.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings


class SemanticScholarError(Exception):
    """An error returned by, or raised while calling, Semantic Scholar."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


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
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=self.timeout,
            ) as client:
                response = client.get(path.lstrip("/"), params=params)
        except httpx.TimeoutException as exc:
            raise SemanticScholarError(
                "Semantic Scholar request timed out", status_code=504
            ) from exc
        except httpx.RequestError as exc:
            raise SemanticScholarError(
                f"Semantic Scholar request failed: {exc}", status_code=502
            ) from exc

        if response.is_error:
            message = response.text[:500] or response.reason_phrase
            try:
                body = response.json()
                if isinstance(body, dict) and body.get("message"):
                    message = str(body["message"])
            except ValueError:
                pass
            raise SemanticScholarError(message, status_code=response.status_code)

        try:
            payload = response.json()
        except ValueError as exc:
            raise SemanticScholarError(
                "Semantic Scholar returned invalid JSON", status_code=502
            ) from exc
        if not isinstance(payload, dict):
            raise SemanticScholarError(
                "Semantic Scholar returned an unexpected response", status_code=502
            )
        return payload

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

        return self._get(path, params)

    def get_paper(self, paper_id: str, *, fields: str) -> dict[str, Any]:
        """Fetch one paper by Semantic Scholar, DOI, arXiv, or Corpus ID."""

        encoded_id = quote(paper_id, safe=":")
        return self._get(f"paper/{encoded_id}", {"fields": fields})
