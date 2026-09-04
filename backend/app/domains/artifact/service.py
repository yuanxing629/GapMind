"""Artifact service 层。

负责文件持久化到本地存储及创建数据库记录。
Phase 1b 仅支持上传路径；删除/list 能力后续加入。
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
    """写入前发现超出 workspace 存储配额时抛出的异常。"""

    def __init__(self, workspace_id: str, quota_bytes: int) -> None:
        super().__init__(f"Workspace storage quota exceeded: {workspace_id}")
        self.workspace_id = workspace_id
        self.quota_bytes = quota_bytes


class ArtifactService:
    """管理文件 artifact 及其数据库记录。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.storage_root = Path(settings.app_storage_dir).resolve()

# ------------------------------------------------------------ 存储
    def _workspace_dir(self, workspace_id: str) -> Path:
        """返回旧版 workspace 级存储目录，不存在时创建。"""
        self._validate_uuid(workspace_id)
# 使用 UUID 的前 2 个字符作为分片子目录，避免后续所有文件集中在同一个目录中。
        shard = workspace_id[:2]
        path = self.storage_root / "workspaces" / shard / workspace_id / "artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _paper_dir(self, workspace_id: str, paper_id: str) -> Path:
        """返回论文隔离的 artifact 目录，不存在时创建。"""
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
        """将上传字节持久化到磁盘，并创建 Artifact 行。

        磁盘文件名使用随机令牌（不是用户提供的名称），避免路径穿越和文件系统编码问题。
        原始文件名保存在 `original_filename` 中。提供 `paper_id` 时，论文所属 Artifact
        隔离在论文目录下；未关联论文的调用方继续使用旧版工作区级路径。
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

# 保存相对路径，以便迁移 storage root。
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

# ----------------------------------------------------------------- 读取
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

# --------------------------------------------------------------- 删除
    def soft_delete(self, artifact_id: str) -> None:
        a = self.get(artifact_id)
        a.is_deleted = True
        self.db.commit()
        logger.info("artifact.soft_deleted", artifact_id=artifact_id)

# ------------------------------------------------------------- 辅助函数
    def resolve_abs_path(self, artifact: Artifact) -> Path:
        """返回 artifact 在磁盘上的绝对路径。"""
        return self.storage_root / artifact.file_path

    @staticmethod
    def _validate_uuid(value: str) -> None:
        try:
            UUID(str(value))
        except (ValueError, TypeError) as e:
            raise ArtifactNotFoundError(value) from e
