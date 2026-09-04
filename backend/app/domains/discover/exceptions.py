"""Discover domain 的异常层次。

集中定义这些异常后，domain 内的任何位置（router、service 子模块、tests）都可以直接
导入，而不必连带加载 ``service.py`` 的其余内容。异常类到 HTTP 状态码的映射位于
``app.core.exception_handlers``。
"""

from __future__ import annotations


class DiscoverInputError(Exception):
    """用户输入无效（缺少 topic、paper_ids 格式错误等）。"""


class DiscoverRunNotFoundError(Exception):
    """不存在给定 id 的 DiscoverRun，或它属于其他 workspace。"""


class DiscoverRunDeletionConflict(Exception):
    """worker 仍处于活动状态时，DiscoverRun 不能删除。"""


class OpportunityNotFoundError(Exception):
    """不存在给定 id 的 ResearchOpportunity，或 workspace 不匹配。"""


class OpportunityVersionConflict(Exception):
    """乐观锁冲突——调用方的 base_version_id 已过期。"""


class InvalidOpportunityTransition(Exception):
    """尝试将 Opportunity 移动到从当前状态不可达的状态。"""


class DiscoverGateError(Exception):
    """确认前的证据门禁失败；``code`` 用于驱动前端提示。

    示例：``insufficient_full_text_evidence``、``coverage_below_threshold``。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DiscoverRunCancelled(Exception):
    """run 在流水线中途被取消时抛出的内部信号。"""


__all__ = [
    "DiscoverInputError",
    "DiscoverRunDeletionConflict",
    "DiscoverRunNotFoundError",
    "OpportunityNotFoundError",
    "OpportunityVersionConflict",
    "InvalidOpportunityTransition",
    "DiscoverGateError",
    "DiscoverRunCancelled",
]
