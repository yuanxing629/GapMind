"""Semantic Scholar 有界重试与失败分类测试。"""

from __future__ import annotations

from collections.abc import Callable

import httpx

from app.core import semantic_scholar_control
from app.core.config import settings
from app.gateway import semantic_scholar
from app.gateway.semantic_scholar import SemanticScholarClient, SemanticScholarError


class _FakeClient:
    def __init__(self, action: Callable[[], httpx.Response | Exception]) -> None:
        self.action = action
        self.calls = 0

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, path: str, *, params: dict[str, object]) -> httpx.Response:
        del path, params
        self.calls += 1
        result = self.action()
        if isinstance(result, Exception):
            raise result
        return result


class _FakePDFResponse:
    def __init__(self, status_code: int, content: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.is_error = status_code >= 400

    def __enter__(self) -> "_FakePDFResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def iter_bytes(self):
        return iter([self.content])


class _FakePDFClient:
    def __init__(self, responses: list[_FakePDFResponse]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def __enter__(self) -> "_FakePDFClient":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def stream(self, method: str, url: str) -> _FakePDFResponse:
        del method, url
        self.calls += 1
        return next(self.responses)


def _response(status_code: int, payload: dict[str, object]) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://api.semanticscholar.org/graph/v1/paper/search"),
    )


def test_retries_transient_5xx_then_returns_payload(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    responses = iter([_response(503, {"message": "busy"}), _response(200, {"data": []})])
    fake = _FakeClient(lambda: next(responses))
    monkeypatch.setattr(semantic_scholar.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(semantic_scholar_control, "wait_for_request_slot", lambda: None)
    monkeypatch.setattr(semantic_scholar, "wait_for_request_slot", lambda: None)
    sleeps: list[float] = []
    monkeypatch.setattr(semantic_scholar.time, "sleep", sleeps.append)
    monkeypatch.setattr(settings, "semantic_scholar_retry_count", 1)
    monkeypatch.setattr(settings, "semantic_scholar_retry_backoff", 0.01)

    payload = SemanticScholarClient()._get("paper/search", {"query": "test"})

    assert payload == {"data": []}
    assert fake.calls == 2
    assert sleeps == [0.01]


def test_retries_timeout_then_raises_with_stable_kind(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    actions = iter([httpx.ReadTimeout("timed out"), httpx.ReadTimeout("timed out")])
    fake = _FakeClient(lambda: next(actions))
    monkeypatch.setattr(semantic_scholar.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(semantic_scholar, "wait_for_request_slot", lambda: None)
    monkeypatch.setattr(semantic_scholar.time, "sleep", lambda _: None)
    monkeypatch.setattr(settings, "semantic_scholar_retry_count", 1)
    monkeypatch.setattr(settings, "semantic_scholar_retry_backoff", 0.01)

    try:
        SemanticScholarClient()._get("paper/search", {"query": "test"})
    except SemanticScholarError as exc:
        assert exc.status_code == 504
        assert exc.failure_kind == "timeout"
        assert exc.attempts == 2
    else:
        raise AssertionError("expected SemanticScholarError")


def test_failure_kind_distinguishes_rate_limit_and_network_error() -> None:
    assert semantic_scholar.semantic_scholar_failure_kind(429) == "rate_limited"
    assert semantic_scholar.semantic_scholar_failure_kind(502) == "network_error"
    assert semantic_scholar.semantic_scholar_failure_kind(503) == "upstream_error"


def test_pdf_download_preserves_http_status_without_retrying_forbidden(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = _FakePDFClient([_FakePDFResponse(403)])
    monkeypatch.setattr(semantic_scholar.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(settings, "semantic_scholar_retry_count", 2)

    try:
        SemanticScholarClient().download_pdf("https://publisher.example/paper.pdf")
    except SemanticScholarError as exc:
        assert exc.status_code == 403
        assert exc.failure_kind == "request_error"
        assert exc.attempts == 1
    else:
        raise AssertionError("expected SemanticScholarError")
    assert fake.calls == 1


def test_pdf_download_retries_transient_status_then_validates_pdf(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake = _FakePDFClient([
        _FakePDFResponse(503),
        _FakePDFResponse(200, b"%PDF-1.7 test"),
    ])
    monkeypatch.setattr(semantic_scholar.httpx, "Client", lambda **kwargs: fake)
    monkeypatch.setattr(settings, "semantic_scholar_retry_count", 1)
    monkeypatch.setattr(settings, "semantic_scholar_retry_backoff", 0.01)
    sleeps: list[float] = []
    monkeypatch.setattr(semantic_scholar.time, "sleep", sleeps.append)

    content = SemanticScholarClient().download_pdf("https://repository.example/paper.pdf")

    assert content.startswith(b"%PDF")
    assert fake.calls == 2
    assert sleeps == [0.01]
