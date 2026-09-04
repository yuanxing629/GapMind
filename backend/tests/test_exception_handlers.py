"""中央错误封装和 exception handler 测试。

这些测试不执行端到端 HTTP layer；它们直接调用 ``_resolve_status`` dispatcher，从而在不
耦合 domain router 的情况下固定 wire format。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import error_envelope
from app.core.exception_handlers import register_exception_handlers
from app.domains.chat.service import (
    ChatConfigurationError,
    ChatNotFoundError,
    ChatUpstreamError,
)
from app.domains.discover.service import DiscoverGateError, DiscoverInputError
from app.domains.knowledge.service import KnowledgeItemNotFoundError
from app.domains.paper.service import PaperAlreadyHasPdfError
from app.gateway.semantic_scholar import SemanticScholarError


def _build_client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/boom/notfound")
    def _raise_not_found() -> None:
        raise ChatNotFoundError("missing")

    @app.get("/boom/discover-input")
    def _raise_discover_input() -> None:
        raise DiscoverInputError("bad input")

    @app.get("/boom/discover-gate")
    def _raise_discover_gate() -> None:
        raise DiscoverGateError("evidence_insufficient", "need more papers")

    @app.get("/boom/paper-conflict")
    def _raise_paper_conflict() -> None:
        raise PaperAlreadyHasPdfError("paper X already has a PDF")

    @app.get("/boom/chat-config")
    def _raise_chat_config() -> None:
        raise ChatConfigurationError(
            "no API key", conversation_id="c1", assistant_message_id="a1"
        )

    @app.get("/boom/chat-upstream")
    def _raise_chat_upstream() -> None:
        raise ChatUpstreamError(
            "remote 502", conversation_id="c2", assistant_message_id=None
        )

    @app.get("/boom/semantic-scholar-429")
    def _raise_s2_429() -> None:
        raise SemanticScholarError("rate limited", status_code=429)

    @app.get("/boom/knowledge-not-found")
    def _raise_knowledge_not_found() -> None:
        raise KnowledgeItemNotFoundError("kid-123")

    return TestClient(app, raise_server_exceptions=False)


def test_envelope_helper_shape():
    envelope = error_envelope("code_x", "msg", retryable=True, foo="bar")
    assert envelope == {
        "detail": {
            "error": "code_x",
            "message": "msg",
            "retryable": True,
            "foo": "bar",
        }
    }


def test_default_envelope_is_not_retryable():
    envelope = error_envelope("code_x", "msg")
    assert envelope["detail"]["retryable"] is False
# 不应泄露额外字段
    assert set(envelope["detail"].keys()) == {"error", "message", "retryable"}


def test_404_not_found_uses_registry_code():
    client = _build_client()
    response = client.get("/boom/notfound")
    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"] == "chat_not_found"
    assert body["detail"]["retryable"] is False


def test_422_input_error_uses_registry_code():
    client = _build_client()
    response = client.get("/boom/discover-input")
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "discover_input_invalid"


def test_discover_gate_carries_dynamic_code():
    client = _build_client()
    response = client.get("/boom/discover-gate")
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "evidence_insufficient"


def test_409_conflict_uses_registry_code():
    client = _build_client()
    response = client.get("/boom/paper-conflict")
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "paper_already_has_pdf"


def test_503_chat_configuration_includes_context():
    client = _build_client()
    response = client.get("/boom/chat-config")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error"] == "llm_unavailable"
    assert detail["conversation_id"] == "c1"
    assert detail["assistant_message_id"] == "a1"


def test_502_chat_upstream_is_retryable():
    client = _build_client()
    response = client.get("/boom/chat-upstream")
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["error"] == "llm_request_failed"
    assert detail["retryable"] is True
    assert detail["conversation_id"] == "c2"
# 异常中的 assistant_message_id 为 None，不应出现在封装中
    assert "assistant_message_id" not in detail


def test_semantic_scholar_passes_through_429():
    client = _build_client()
    response = client.get("/boom/semantic-scholar-429")
    assert response.status_code == 429
    assert response.json()["detail"]["error"] == "semantic_scholar_error"


def test_knowledge_item_not_found():
    client = _build_client()
    response = client.get("/boom/knowledge-not-found")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "knowledge_item_not_found"


def test_envelope_is_documented_pydantic_model():
    """固定前端 codegen 依赖的公开 schema。"""
    from app.core.errors import ErrorDetail, ErrorResponse

    detail = ErrorDetail(error="x", message="m", retryable=False, custom_field=42)
    response = ErrorResponse(detail=detail)
    dumped = response.model_dump()
    assert dumped == {
        "detail": {
            "error": "x",
            "message": "m",
            "retryable": False,
            "custom_field": 42,
        }
    }
