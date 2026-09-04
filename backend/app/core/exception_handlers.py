"""集中式 FastAPI 异常处理器。

在本模块出现之前，每个路由都重复实现相同的 ``try /
except X -> raise HTTPException(detail={"error": ..., "message": ...})``
流程（最明显的例子见 ``chat/router.py``：相同的 502/503 映射被复制了三次）。
这使错误信封的演进必须触及几十个调用位置。

本模块提供单一分发器和注册表。添加新的领域异常只需向
``EXCEPTION_REGISTRY`` 追加一行，其他无需改动，路由可以保持简洁。
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


# （status_code、error_code、retryable）
# 按 domain 排序，便于阅读。
EXCEPTION_REGISTRY: dict[type[Exception], tuple[int, str, bool]] = {
    # 404 — 未找到
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
    # 409 — 冲突 / 状态机
    ChatConflictError: (409, "chat_conflict", False),
    DiscoverRunDeletionConflict: (409, "discover_run_deletion_conflict", False),
    InvalidOpportunityTransition: (409, "invalid_opportunity_transition", False),
    InvalidTaskTransition: (409, "invalid_task_transition", False),
    OpportunityVersionConflict: (409, "opportunity_version_conflict", False),
    PaperAlreadyHasPdfError: (409, "paper_already_has_pdf", False),
    AgentConflictError: (409, "agent_conflict", False),
    # 422 — 输入校验
    ChatInputError: (400, "invalid_chat_input", False),
    DiscoverInputError: (422, "discover_input_invalid", False),
    KnowledgeItemReviewError: (422, "invalid_review", False),
    AgentInputError: (422, "agent_input_invalid", False),
}


def _extras_for_chat(exc: ChatConfigurationError | ChatUpstreamError | ChatRetrievalError) -> dict[str, str]:
    """将 Chat 上下文（会话和 assistant 消息 ID）带入错误信封。"""
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
    """暴露变更前后的状态，使前端可以渲染准确提示。"""
    return {"from_status": exc.from_status, "to_status": exc.to_status}


def _resolve_status(exc: Exception) -> tuple[int, str, bool, dict[str, str]]:
    """为 ``exc`` 返回 ``(status_code, error_code, retryable, extras)``。

    特殊情况（Chat 上游错误和 Discover 门禁错误）携带自身元数据；
    其他情况沿用静态 ``EXCEPTION_REGISTRY``。
    """
    # Chat LLM 错误天然可重试；上下文随异常传递。
    if isinstance(exc, ChatConfigurationError):
        return 503, "llm_unavailable", False, _extras_for_chat(exc)
    if isinstance(exc, ChatUpstreamError):
        return 502, "llm_request_failed", True, _extras_for_chat(exc)
    if isinstance(exc, ChatRetrievalError):
        return 502, "workspace_retrieval_failed", True, _extras_for_chat(exc)

    # Discover gate 错误暴露自身的 `code`，以便前端展示精确的修复提示
    # （例如 "evidence_insufficient"）。
    if isinstance(exc, DiscoverGateError):
        return 422, exc.code, False, {}

    # Task 状态机错误携带产生问题的 from/to status，以便 UI 告知用户
    # “不能将已 succeeded 的 task 移回 queued”。
    if isinstance(exc, InvalidTaskTransition):
        return 409, "invalid_task_transition", False, _extras_for_task_transition(exc)

    # Semantic Scholar 上游错误根据客户端捕获的上游 status 映射为 502/504
    # （或透传 4xx）。
    if isinstance(exc, SemanticScholarError):
        upstream = exc.status_code
        if upstream in {400, 401, 403, 429}:
            response_status = upstream
        elif upstream == 504:
            response_status = 504
        else:
            response_status = 502
        return response_status, "semantic_scholar_error", True, {}

    # 遍历 registry。线性扫描即可，因为这里只有约 20 项。
    for cls, (status_code, code, retryable) in EXCEPTION_REGISTRY.items():
        if isinstance(exc, cls):
            return status_code, code, retryable, {}

    # Unknown — 让 FastAPI 渲染默认的 500。此处返回自定义 envelope 会掩盖编程错误，
    # 并使其无法出现在 Sentry/日志告警中。
    raise exc


async def domain_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """FastAPI 为每个已注册的领域异常调用的处理器。"""
    status_code, code, retryable, extras = _resolve_status(exc)
    return JSONResponse(
        status_code=status_code,
        content=error_envelope(code, str(exc), retryable=retryable, **extras),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """将 ``domain_exception_handler`` 附加到每个已注册的异常类。

    FastAPI 会优先匹配更具体的异常，因此逐个注册每个类，效果等同于注册单个
    ``Exception`` 处理器，同时还能让 FastAPI 原生处理其 ``HTTPException`` /
    ``RequestValidationError`` 路径。
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
