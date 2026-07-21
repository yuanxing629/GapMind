"""Artifact service layer.

Handles file persistence to local storage and DB record creation.
Phase 1b only supports the upload path; deletion / list is added later.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
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


class ArtifactService:
    """Manages file artifacts and their DB records."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.storage_root = Path(settings.app_storage_dir).resolve()

    # ------------------------------------------------------------ storage
    def _workspace_dir(self, workspace_id: str) -> Path:
        """Return the storage dir for a workspace, creating it if needed."""
        self._validate_uuid(workspace_id)
        # Use first 2 chars of UUID as a sharding subdirectory to avoid
        # thousands of files in a single directory later.
        shard = workspace_id[:2]
        path = self.storage_root / "workspaces" / shard / workspace_id / "artifacts"
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
    ) -> Artifact:
        """Persist uploaded bytes to disk and create an Artifact row.

        The on-disk filename is a random token (not the user-supplied name)
        to avoid path traversal and filesystem encoding issues. The original
        filename is preserved in `original_filename`.
        """
        if not content:
            raise ValueError("Uploaded file is empty")

        ws_dir = self._workspace_dir(workspace_id)
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

    def list_by_workspace(self, workspace_id: str, *, kind: str | None = None) -> list[Artifact]:
        q = select(Artifact).where(
            Artifact.workspace_id == workspace_id,
            Artifact.is_deleted.is_(False),
        )
        if kind is not None:
            q = q.where(Artifact.kind == kind)
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
