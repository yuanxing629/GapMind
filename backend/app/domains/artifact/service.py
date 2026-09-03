"""Artifact service layer.

Handles file persistence to local storage and DB record creation.
Phase 1b only supports the upload path; deletion / list is added later.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.domains.artifact.models import Artifact
from app.domains.artifact.schemas import ArtifactCreateInternal

logger = get_logger(__name__)


class ArtifactNotFoundError(Exception):
    def __init__(self, artifact_id: str) -> None:
        super().__init__(f"Artifact not found: {artifact_id}")
        self.artifact_id = artifact_id


class ArtifactQuotaExceededError(Exception):
    """Raised before writing when a workspace storage quota would be exceeded."""

    def __init__(self, workspace_id: str, quota_bytes: int) -> None:
        super().__init__(f"Workspace storage quota exceeded: {workspace_id}")
        self.workspace_id = workspace_id
        self.quota_bytes = quota_bytes


class ArtifactService:
    """Manages file artifacts and their DB records."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.storage_root = Path(settings.app_storage_dir).resolve()

    # ------------------------------------------------------------ storage
    def _workspace_dir(self, workspace_id: str) -> Path:
        """Return the legacy workspace-level storage dir, creating it if needed."""
        self._validate_uuid(workspace_id)
        # Use first 2 chars of UUID as a sharding subdirectory to avoid
        # thousands of files in a single directory later.
        shard = workspace_id[:2]
        path = self.storage_root / "workspaces" / shard / workspace_id / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _paper_dir(self, workspace_id: str, paper_id: str) -> Path:
        """Return the paper-isolated artifact dir, creating it if needed."""
        self._validate_uuid(workspace_id)
        self._validate_uuid(paper_id)
        shard = workspace_id[:2]
        path = (
            self.storage_root
            / "workspaces"
            / shard
            / workspace_id
            / "papers"
            / paper_id
            / "artifacts"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_upload(
        self,
        *,
        workspace_id: str,
        filename: str,
        content: bytes,
        mime_type: str | None = None,
        kind: str = "pdf",
        paper_id: str | None = None,
    ) -> Artifact:
        """Persist uploaded bytes to disk and create an Artifact row.

        The on-disk filename is a random token (not the user-supplied name)
        to avoid path traversal and filesystem encoding issues. The original
        filename is preserved in `original_filename`. Paper-owned artifacts
        are isolated under the paper directory when `paper_id` is provided;
        callers without a paper keep the legacy workspace-level path.
        """
        if not content:
            raise ValueError("Uploaded file is empty")

        current_bytes = int(
            self.db.execute(
                select(func.coalesce(func.sum(Artifact.size_bytes), 0)).where(
                    Artifact.workspace_id == workspace_id,
                    Artifact.is_deleted.is_(False),
                )
            ).scalar()
            or 0
        )
        if current_bytes + len(content) > settings.workspace_storage_quota_bytes:
            raise ArtifactQuotaExceededError(
                workspace_id,
                settings.workspace_storage_quota_bytes,
            )

        ws_dir = (
            self._paper_dir(workspace_id, paper_id)
            if paper_id is not None
            else self._workspace_dir(workspace_id)
        )
        token = secrets.token_hex(8)
        safe_ext = Path(filename).suffix.lower()[:16] if filename else ""
        stored_name = f"{token}{safe_ext}"
        file_path = ws_dir / stored_name
        file_path.write_bytes(content)

        # Store a relative path so the storage root can be relocated.
        rel_path = str(file_path.relative_to(self.storage_root)).replace("\\", "/")

        artifact = Artifact(
            id=str(uuid4()),
            workspace_id=workspace_id,
            kind=kind,
            file_path=rel_path,
            original_filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            is_deleted=False,
        )
        self.db.add(artifact)
        self.db.commit()
        self.db.refresh(artifact)
        logger.info(
            "artifact.saved",
            artifact_id=artifact.id,
            workspace_id=workspace_id,
            kind=kind,
            size_bytes=artifact.size_bytes,
        )
        return artifact

    # ----------------------------------------------------------------- read
    def get(self, artifact_id: str) -> Artifact:
        self._validate_uuid(artifact_id)
        a = self.db.get(Artifact, artifact_id)
        if a is None or a.is_deleted:
            raise ArtifactNotFoundError(artifact_id)
        return a

    def list_by_workspace(
        self,
        workspace_id: str,
        *,
        kind: str | None = None,
        paper_id: str | None = None,
    ) -> list[Artifact]:
        self._validate_uuid(workspace_id)
        q = select(Artifact).where(
            Artifact.workspace_id == workspace_id,
            Artifact.is_deleted.is_(False),
        )
        if kind is not None:
            q = q.where(Artifact.kind == kind)
        if paper_id is not None:
            self._validate_uuid(paper_id)
            shard = workspace_id[:2]
            prefix = (
                f"workspaces/{shard}/{workspace_id}/papers/{paper_id}/artifacts/"
            )
            q = q.where(Artifact.file_path.startswith(prefix))
        return list(self.db.execute(q).scalars().all())

    # --------------------------------------------------------------- delete
    def soft_delete(self, artifact_id: str) -> None:
        a = self.get(artifact_id)
        a.is_deleted = True
        self.db.commit()
        logger.info("artifact.soft_deleted", artifact_id=artifact_id)

    # ------------------------------------------------------------- helpers
    def resolve_abs_path(self, artifact: Artifact) -> Path:
        """Return absolute on-disk path for an artifact."""
        return self.storage_root / artifact.file_path

    @staticmethod
    def _validate_uuid(value: str) -> None:
        try:
            UUID(str(value))
        except (ValueError, TypeError) as e:
            raise ArtifactNotFoundError(value) from e
