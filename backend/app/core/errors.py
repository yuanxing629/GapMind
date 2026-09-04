"""GapMind API 共用的错误封装辅助函数。

异常处理器发出的所有错误响应都应经过 `error_envelope()`，从而保证：

* wire format 保持稳定：``{"detail": {"error": code, "message": msg,
    "retryable": bool, **extra}}``
* Pydantic ``ErrorDetail`` / ``ErrorResponse`` schema 在 OpenAPI 中记录该结构，供前端代码生成使用。

前端代码（以及 ``docs/architecture-refactor-plan-2026-08-04.md`` 契约）按
``response.data.detail.error`` / ``.message`` / ``.retryable`` 及
``conversation_id``、``assistant_message_id`` 等额外上下文读取错误。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorDetail(BaseModel):
    """标准错误详情载荷。

    ``ConfigDict(extra="allow")`` 保持 schema 开放，使 domain-specific context（例如
    ``conversation_id``、``run_id``）可以随响应传递，而不要求每个 handler 都在模型中声明。
    """

    model_config = ConfigDict(extra="allow")

    error: str
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    """所有 4xx/5xx handler 使用的标准错误响应封装。"""

    detail: ErrorDetail


def error_envelope(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    """构建标准错误封装字典。

    形状与上面的 Pydantic ``ErrorResponse`` 模型一致；手动保持二者一致，
    是前端可以编写单一拦截器的契约。
    """
    detail: dict[str, Any] = {
        "error": code,
        "message": message,
        "retryable": retryable,
    }
    detail.update(extra)
    return {"detail": detail}
