"""Centralised FastAPI exception handlers.

Before this module existed, every router reimplemented the same ``try /
except X → raise HTTPException(detail={"error": ..., "message": ...})``
dance (see ``chat/router.py`` for the worst offender: the same 502/503
mapping copy-pasted three times). That made it impossible to evolve the
error envelope without touching dozens of call sites.

This module exposes a single dispatcher plus a registry. Adding a new
domain exception means appending one line to ``EXCEPTION_REGISTRY`` and
nothing else — the router stays clean.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import error_envelope
from app.domains.artifact.service import ArtifactNotFoundError, ArtifactQuotaExceededError
from app.gateway.semantic_scholar import SemanticScholarError
from app.domains.chat.service import (
    ChatConfigurationError,
    ChatConflictError,
    ChatInputError,
    ChatNotFoundError,
    ChatRetrievalError,
    ChatUpstreamError,
)
from app.domains.discover.service import (
    DiscoverGateError,
    DiscoverInputError,
    DiscoverRunDeletionConflict,
    DiscoverRunNotFoundError,
    InvalidOpportunityTransition,
    OpportunityNotFoundError,
    OpportunityVersionConflict,
)
from app.domains.knowledge.service import (
    ExtractionRunNotFoundError,
    KnowledgeItemNotFoundError,
    KnowledgeItemReviewError,
)
from app.domains.paper.service import (
    PaperAlreadyHasPdfError,
    PaperNotFoundError,
)
from app.domains.task.service import InvalidTaskTransition, TaskNotFoundError
from app.domains.workspace.service import WorkspaceNotFoundError
from app.domains.agent.service import (
    AgentConflictError,
    AgentInputError,
    AgentRunNotFoundError,
)


# (status_code, error_code, retryable)
# Keep entries sorted by domain for readability.
EXCEPTION_REGISTRY: dict[type[Exception], tuple[int, str, bool]] = {
    # 404 — Not Found
    ArtifactNotFoundError: (404, "artifact_not_found", False),
    ArtifactQuotaExceededError: (413, "workspace_storage_quota_exceeded", False),
    ChatNotFoundError: (404, "chat_not_found", False),
    DiscoverRunNotFoundError: (404, "discover_run_not_found", False),
    ExtractionRunNotFoundError: (404, "extraction_run_not_found", False),
    KnowledgeItemNotFoundError: (404, "knowledge_item_not_found", False),
    OpportunityNotFoundError: (404, "opportunity_not_found", False),
    PaperNotFoundError: (404, "paper_not_found", False),
    TaskNotFoundError: (404, "task_not_found", False),
    WorkspaceNotFoundError: (404, "workspace_not_found", False),
    AgentRunNotFoundError: (404, "agent_run_not_found", False),
    # 409 — Conflict / state machine
    ChatConflictError: (409, "chat_conflict", False),
    DiscoverRunDeletionConflict: (409, "discover_run_deletion_conflict", False),
    InvalidOpportunityTransition: (409, "invalid_opportunity_transition", False),
    InvalidTaskTransition: (409, "invalid_task_transition", False),
    OpportunityVersionConflict: (409, "opportunity_version_conflict", False),
    PaperAlreadyHasPdfError: (409, "paper_already_has_pdf", False),
    AgentConflictError: (409, "agent_conflict", False),
    # 422 — Input validation
    ChatInputError: (400, "invalid_chat_input", False),
    DiscoverInputError: (422, "discover_input_invalid", False),
    KnowledgeItemReviewError: (422, "invalid_review", False),
    AgentInputError: (422, "agent_input_invalid", False),
}


def _extras_for_chat(exc: ChatConfigurationError | ChatUpstreamError | ChatRetrievalError) -> dict[str, str]:
    """Carry the chat context (conversation + assistant message IDs) into the envelope."""
    extras: dict[str, str] = {}
    if exc.conversation_id is not None:
        extras["conversation_id"] = exc.conversation_id
    if exc.assistant_message_id is not None:
        extras["assistant_message_id"] = exc.assistant_message_id
    diagnostic_code = getattr(exc, "diagnostic_code", None)
    if diagnostic_code:
        extras["diagnostic_code"] = diagnostic_code
    return extras


def _extras_for_task_transition(exc: InvalidTaskTransition) -> dict[str, str]:
    """Expose from/to status so the front-end can render a precise hint."""
    return {"from_status": exc.from_status, "to_status": exc.to_status}


def _resolve_status(exc: Exception) -> tuple[int, str, bool, dict[str, str]]:
    """Return ``(status_code, error_code, retryable, extras)`` for ``exc``.

    Special cases (chat upstream + discover gate) carry their own metadata;
    everything else falls through to the static ``EXCEPTION_REGISTRY``.
    """
    # Chat LLM errors — retryable by nature; context travels with the exception.
    if isinstance(exc, ChatConfigurationError):
        return 503, "llm_unavailable", False, _extras_for_chat(exc)
    if isinstance(exc, ChatUpstreamError):
        return 502, "llm_request_failed", True, _extras_for_chat(exc)
    if isinstance(exc, ChatRetrievalError):
        return 502, "workspace_retrieval_failed", True, _extras_for_chat(exc)

    # Discover gate errors expose their own `code` so the front-end can render
    # a precise remediation hint (e.g. "evidence_insufficient").
    if isinstance(exc, DiscoverGateError):
        return 422, exc.code, False, {}

    # Task state-machine errors carry the offending from/to status so the
    # UI can tell the user "you can't move a succeeded task back to queued".
    if isinstance(exc, InvalidTaskTransition):
        return 409, "invalid_task_transition", False, _extras_for_task_transition(exc)

    # Semantic Scholar upstream errors map to a 502/504 (or pass-through 4xx)
    # depending on the upstream status the client captured.
    if isinstance(exc, SemanticScholarError):
        upstream = exc.status_code
        if upstream in {400, 401, 403, 429}:
            response_status = upstream
        elif upstream == 504:
            response_status = 504
        else:
            response_status = 502
        return response_status, "semantic_scholar_error", True, {}

    # Walk the registry. Linear scan is fine — there are ~20 entries.
    for cls, (status_code, code, retryable) in EXCEPTION_REGISTRY.items():
        if isinstance(exc, cls):
            return status_code, code, retryable, {}

    # Unknown — let FastAPI render its default 500. Returning a custom envelope
    # here would mask programming errors and hide them from Sentry/log alerts.
    raise exc


async def domain_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI handler invoked for every registered domain exception."""
    status_code, code, retryable, extras = _resolve_status(exc)
    return JSONResponse(
        status_code=status_code,
        content=error_envelope(code, str(exc), retryable=retryable, **extras),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Attach ``domain_exception_handler`` to every registered exception class.

    FastAPI walks the registry most-specific-first, so registering each class
    individually is the same as registering a single ``Exception`` handler
    but lets FastAPI still handle its own ``HTTPException`` /
    ``RequestValidationError`` paths untouched.
    """
    seen: set[type[Exception]] = set()
    for exc_cls in (
        ChatConfigurationError,
        ChatUpstreamError,
        ChatRetrievalError,
        DiscoverGateError,
        SemanticScholarError,
        *EXCEPTION_REGISTRY.keys(),
    ):
        if exc_cls in seen:
            continue
        seen.add(exc_cls)
        app.add_exception_handler(exc_cls, domain_exception_handler)
