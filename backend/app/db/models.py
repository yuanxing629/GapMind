"""Central import point for all ORM models.

Importing this module ensures SQLAlchemy/Alembic can discover every model
via `Base.metadata`. Add new models here as they are created.
"""

from __future__ import annotations

# Phase 1a: Workspace domain
from app.domains.workspace.models import Workspace  # noqa: F401

# Phase 1b: Artifact, Paper, Task, Timeline, Knowledge
from app.domains.artifact.models import Artifact  # noqa: F401
from app.domains.knowledge.models import (  # noqa: F401
    EvidenceSpan,
    KnowledgeItem,
    KnowledgeRelation,
)
from app.domains.paper.models import Paper  # noqa: F401
from app.domains.task.models import Task  # noqa: F401
from app.domains.timeline.models import TimelineEvent  # noqa: F401

from app.db.base import Base  # noqa: F401
