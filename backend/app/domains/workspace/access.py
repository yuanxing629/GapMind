"""Workspace ownership policy shared by middleware and domain services."""

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
    """Return whether ``user_id`` owns the workspace.

    ``minimum_role`` is retained for call-site compatibility during the
    owner-only migration. There are no member roles in this product model.
    """
    del minimum_role
    workspace = db.get(Workspace, workspace_id)
    return workspace is not None and not workspace.is_deleted and workspace.owner_id == user_id
