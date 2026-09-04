"""所有 ORM 模型的集中导入入口。

导入本模块可确保 SQLAlchemy/Alembic 通过 `Base.metadata` 发现所有模型。
新增模型时在这里加入导入。
"""

from __future__ import annotations

from app.db.base import Base  # noqa: F401
from app.domains.agent.models import AgentArtifact, AgentRun, AgentStep  # noqa: F401
from app.domains.auth.models import (  # noqa: F401
    AuthAuditEvent,
    PasswordResetToken,
    User,
    UserInvite,
    UserRole,
    UserSession,
)

# Phase 1b：Artifact、Paper、Task、Timeline、Knowledge
from app.domains.artifact.models import Artifact  # noqa: F401
from app.domains.chat.models import (  # noqa: F401
    ChatConversation,
    ChatMessage,
    ChatMessageEvidence,
    ChatMessageImage,
)
from app.domains.discover.models import (  # noqa: F401
    DiscoverExternalCandidate,
    DiscoverRun,
    HumanDecision,
    OpportunityEvidence,
    OpportunityVersion,
    ResearchOpportunity,
    ResearchPlan,
)
from app.domains.gap.models import (  # noqa: F401
    GapBoardSnapshot,
    GapCanonicalConcept,
    GapConceptAssignment,
    PaperGapAnnotation,
)
from app.domains.knowledge.models import (  # noqa: F401
    CanonicalEntity,
    EvidenceSpan,
    ExtractionRejection,
    ExtractionRun,
    KnowledgeItem,
    KnowledgeRelation,
    PaperMention,
)
from app.domains.paper.models import Paper  # noqa: F401
from app.domains.paper.search_models import (  # noqa: F401
    PaperSearchFavorite,
    PaperSearchHistory,
)
from app.domains.reading.models import PaperAnnotation, ReadingItem  # noqa: F401
from app.domains.recommendation.models import PaperRecommendation  # noqa: F401
from app.domains.task.models import Task  # noqa: F401
from app.domains.timeline.models import TimelineEvent  # noqa: F401

# Phase 1a：Workspace 领域
from app.domains.workspace.models import Workspace  # noqa: F401
