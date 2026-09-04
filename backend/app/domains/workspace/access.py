"""middleware 与 domain service 共用的 workspace 所有权策略。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.domains.workspace.models import Workspace


def has_workspace_access(
    db: Session,
    workspace_id: str,
    user_id: str,
    *,
    minimum_role: str = "viewer",
) -> bool:
    """返回 ``user_id`` 是否拥有该 workspace。

    ``minimum_role`` 在 owner-only 迁移期间保留，用于兼容调用点。当前产品模型没有成员角色。
    """
    del minimum_role
    workspace = db.get(Workspace, workspace_id)
    return workspace is not None and not workspace.is_deleted and workspace.owner_id == user_id
