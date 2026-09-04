"""使用 structlog 的结构化日志配置。"""

from __future__ import annotations

import logging
import sys

import structlog

from app.core.config import settings


def configure_logging() -> None:
    """在启动时一次性配置 structlog 和标准库 logging。"""
    level = getattr(logging, settings.app_log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """返回绑定上下文的 structlog logger。"""
    return structlog.get_logger(name)
