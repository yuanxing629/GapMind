"""工作区科研就绪度聚合（W0）。

作为“能否运行 Discover / 为什么不能 / 下一步去哪里”的唯一事实来源。

五个维度——corpus、retrieval、knowledge、discover、research——分别报告
``ready`` / ``waiting`` / ``blocked``，并提供指向解除阻塞页面的可理解操作。
概览进度条完全由此端点驱动；这里的每个计数都是真实的 ``func.count()``
（不是前端分页总数），因此各页面上的数字保持一致。

设计说明：
- 维度状态：``ready``（可用）、``waiting``（前置条件已满足但后台流水线任务仍在运行，
  不是需要用户处理的阻塞项）、``blocked``（用户必须操作；blocking_actions 说明原因和位置）。
- ``recommended_next_action`` 是首个非 ready 维度的首个阻塞操作，即概览页展示的唯一“下一步做什么”。
- Milvus 分块数量采用尽力获取策略：Milvus 故障时降级为 ``None``，而不是让整个就绪度端点失败
  （遵循 W5 降级原则）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.domains.discover.models import DiscoverRun, ResearchOpportunity, ResearchPlan
from app.domains.knowledge.models import KnowledgeItem
from app.domains.paper.models import Paper
from app.domains.task.models import Task
from app.domains.workspace.models import Workspace

# 状态语义与 discover/service.py 及 task domain 保持同步。
TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled"}
WAITING_RUN_STATUSES = {"waiting_for_user", "waiting_for_fulltext"}
PIPELINE_PENDING_STATUSES = {"queued", "running", "waiting_for_user"}
CLOSED_OPPORTUNITY_STATUSES = {"confirmed", "edited_confirmed", "rejected"}
CONFIRMED_OPPORTUNITY_STATUSES = {"confirmed", "edited_confirmed"}
# 表示“系统仍在处理”的后台流水线任务。
PIPELINE_TASK_TYPES = ("parse_pdf", "extract_knowledge", "embed_chunks")
# 抽取已产生但尚未人工确认的知识状态。
KNOWLEDGE_PENDING_STATUSES = ("extracted_candidate", "evidence_backed_proposal")


class WorkspaceReadinessService:
    """将工作区科研就绪度聚合为一个可解释对象。"""

    def __init__(self, db: Session) -> None:
        self.db = db

# ------------------------------------------------------------------ 入口
    def get_readiness(self, workspace: Workspace) -> dict[str, Any]:
        """返回工作区的完整就绪度文档。"""
        counts = self._counts(workspace.id)
        profile_set = self._profile_set(workspace)
        dimensions = [
            self._corpus(counts, workspace.id),
            self._retrieval(counts, workspace.id),
            self._knowledge(counts, workspace.id),
            self._discover(counts, workspace, profile_set),
            self._research(counts, workspace.id),
        ]
        return {
            "workspace_id": workspace.id,
            "counts": counts,
            "dimensions": dimensions,
            "recommended_next_action": self._recommended(dimensions, counts, workspace.id),
        }

# ----------------------------------------------------------------- 计数
    def _counts(self, workspace_id: str) -> dict[str, int]:
        def count(q: Any) -> int:
            return int(self.db.execute(q).scalar() or 0)

        papers_q = Paper.workspace_id == workspace_id
# Opportunity 计数必须与 list_opportunities 一致：Discover run 已软删除的
# opportunity 对 UI 隐藏。
        visible_opportunities = (
            select(ResearchOpportunity.id, ResearchOpportunity.status)
            .outerjoin(DiscoverRun, ResearchOpportunity.discover_run_id == DiscoverRun.id)
            .where(
                ResearchOpportunity.workspace_id == workspace_id,
                ResearchOpportunity.is_deleted.is_(False),
                or_(
                    ResearchOpportunity.discover_run_id.is_(None),
                    DiscoverRun.deleted_at.is_(None),
                ),
            )
            .subquery()
        )
        return {
            "papers": count(
                select(func.count()).select_from(Paper).where(papers_q, Paper.is_deleted.is_(False))
            ),
            "papers_with_pdf": count(
                select(func.count())
                .select_from(Paper)
                .where(papers_q, Paper.is_deleted.is_(False), Paper.primary_artifact_id.is_not(None))
            ),
            "parsed_papers": count(
                select(func.count())
                .select_from(Paper)
                .where(papers_q, Paper.is_deleted.is_(False), Paper.parse_status == "parsed")
            ),
            "extracted_papers": count(
                select(func.count())
                .select_from(Paper)
                .where(papers_q, Paper.is_deleted.is_(False), Paper.extract_status == "extracted")
            ),
            "knowledge_items": count(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(KnowledgeItem.workspace_id == workspace_id, KnowledgeItem.is_deleted.is_(False))
            ),
            "confirmed_items": count(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(
                    KnowledgeItem.workspace_id == workspace_id,
                    KnowledgeItem.is_deleted.is_(False),
                    KnowledgeItem.status == "human_confirmed",
                )
            ),
            "pending_knowledge": count(
                select(func.count())
                .select_from(KnowledgeItem)
                .where(
                    KnowledgeItem.workspace_id == workspace_id,
                    KnowledgeItem.is_deleted.is_(False),
                    KnowledgeItem.status.in_(KNOWLEDGE_PENDING_STATUSES),
                )
            ),
            "runs": count(
                select(func.count())
                .select_from(DiscoverRun)
                .where(DiscoverRun.workspace_id == workspace_id, DiscoverRun.deleted_at.is_(None))
            ),
            "pending_runs": count(
                select(func.count())
                .select_from(DiscoverRun)
                .where(
                    DiscoverRun.workspace_id == workspace_id,
                    DiscoverRun.deleted_at.is_(None),
                    or_(
                        DiscoverRun.status.in_(WAITING_RUN_STATUSES),
                        DiscoverRun.status.in_(PIPELINE_PENDING_STATUSES),
                    ),
                )
            ),
# Opportunity 计数必须与 list_opportunities 一致：Discover run 已软删除的
# opportunity 对 UI 隐藏。
            "opportunities": count(
                select(func.count()).select_from(visible_opportunities)
            ),
            "pending_opportunities": count(
                select(func.count())
                .select_from(visible_opportunities)
                .where(visible_opportunities.c.status.not_in(CLOSED_OPPORTUNITY_STATUSES))
            ),
            "confirmed_opportunities": count(
                select(func.count())
                .select_from(visible_opportunities)
                .where(visible_opportunities.c.status.in_(CONFIRMED_OPPORTUNITY_STATUSES))
            ),
            "research_plans": count(
                select(func.count()).select_from(ResearchPlan).where(ResearchPlan.workspace_id == workspace_id)
            ),
            "active_tasks": count(
                select(func.count())
                .select_from(Task)
                .where(
                    Task.workspace_id == workspace_id,
                    Task.is_deleted.is_(False),
                    Task.task_type.in_(PIPELINE_TASK_TYPES),
                    Task.status.in_(PIPELINE_PENDING_STATUSES),
                )
            ),
        }

    def _indexed_chunks(self, workspace_id: str) -> int | None:
        """尽力获取 Milvus 分块数量；服务故障时降级为 None。"""
        try:
            from app.domains.retrieval.milvus_client import MilvusClient

            return MilvusClient().count_by_workspace(workspace_id)
        except Exception:
            return None

    @staticmethod
    def _profile_set(workspace: Workspace) -> bool:
        return bool(
            (workspace.topic or "").strip()
            or (workspace.goals or "").strip()
            or any(q.strip() for q in workspace.active_questions)
        )

# ------------------------------------------------------------- 维度
    def _corpus(self, c: dict[str, int], workspace_id: str) -> dict[str, Any]:
        ready = c["parsed_papers"] >= 1
        waiting = (not ready) and c["papers"] > 0 and c["active_tasks"] > 0
        blocking: list[dict[str, str]] = []
        if c["papers"] == 0:
            blocking.append(self._action("添加论文", "还没有任何论文作为证据基础。", f"/workspaces/{workspace_id}/papers"))
        elif not ready and not waiting:
            blocking.append(self._action("等待或重试 PDF 解析", "已有论文但尚未完成解析。", f"/workspaces/{workspace_id}/activity"))
        return self._dimension(
            "corpus", "文献", ready, waiting,
            f"{c['papers']} 篇论文 · {c['parsed_papers']} 篇已解析",
            blocking,
        )

    def _retrieval(self, c: dict[str, int], workspace_id: str) -> dict[str, Any]:
        ready = c["extracted_papers"] >= 1
        waiting = (not ready) and c["parsed_papers"] > 0 and c["active_tasks"] > 0
        blocking: list[dict[str, str]] = []
        if c["parsed_papers"] == 0 and c["papers"] > 0:
            blocking.append(self._action("等待论文解析", "解析完成后才能抽取知识与建立索引。", f"/workspaces/{workspace_id}/activity"))
        elif not ready and not waiting:
            blocking.append(self._action("运行知识抽取与索引", "没有可用于检索的抽取结果。", f"/workspaces/{workspace_id}/activity"))
        chunks = self._indexed_chunks(workspace_id)
        chunk_text = f" · {chunks} chunks" if chunks is not None else ""
        return self._dimension(
            "retrieval", "检索", ready, waiting,
            f"{c['extracted_papers']} 篇已抽取{chunk_text}",
            blocking,
        )

    def _knowledge(self, c: dict[str, int], workspace_id: str) -> dict[str, Any]:
        ready = c["knowledge_items"] >= 1
        waiting = (not ready) and c["extracted_papers"] > 0 and c["active_tasks"] > 0
        blocking: list[dict[str, str]] = []
        if not ready and not waiting:
            blocking.append(self._action("等待知识抽取", "当前还没有可用的知识条目。", f"/workspaces/{workspace_id}/activity"))
        return self._dimension(
            "knowledge", "知识", ready, waiting,
            f"{c['knowledge_items']} 条知识 · {c['confirmed_items']} 条已确认",
            blocking,
        )

    def _discover(self, c: dict[str, int], workspace: Workspace, profile_set: bool) -> dict[str, Any]:
        retrieval_ready = c["extracted_papers"] >= 1
        knowledge_ready = c["knowledge_items"] >= 1
        ready = profile_set and retrieval_ready and knowledge_ready
        waiting = (not ready) and c["pending_runs"] > 0
        workspace_id = workspace.id
        blocking: list[dict[str, str]] = []
        if not profile_set:
            blocking.append(self._action("设置研究主题与问题", "Discover 需要研究主题、目标或研究问题。", f"/workspaces/{workspace_id}/settings"))
        if not retrieval_ready:
            blocking.append(self._action("等待检索就绪", "先完成论文抽取与索引才能检索。", f"/workspaces/{workspace_id}/activity"))
        if not knowledge_ready:
            blocking.append(self._action("等待知识就绪", "先抽取知识才能作为发现输入。", f"/workspaces/{workspace_id}/activity"))
        return self._dimension(
            "discover", "发现", ready, waiting,
            f"{c['runs']} 次运行 · {c['pending_runs']} 项待处理",
            blocking,
        )

    def _research(self, c: dict[str, int], workspace_id: str) -> dict[str, Any]:
        ready = c["confirmed_opportunities"] >= 1 or c["research_plans"] >= 1
        blocking: list[dict[str, str]] = []
        if not ready:
            if c["pending_opportunities"] > 0:
                blocking.append(self._action("处理待确认机会", "有人工待确认的研究机会。", f"/workspaces/{workspace_id}/discover"))
            else:
                blocking.append(self._action("运行 Discover 并确认机会", "先产生候选，再人工确认一个研究方向。", f"/workspaces/{workspace_id}/discover"))
        return self._dimension(
            "research", "研究", ready, False,
            f"{c['confirmed_opportunities']} 个已确认机会 · {c['research_plans']} 个研究计划",
            blocking,
        )

# ---------------------------------------------------------------- 辅助函数
    @staticmethod
    def _dimension(
        key: str,
        label: str,
        ready: bool,
        waiting: bool,
        summary: str,
        blocking_actions: list[dict[str, str]],
    ) -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "ready": ready,
            "waiting": waiting,
            "summary": summary,
            "blocking_actions": blocking_actions,
        }

    @staticmethod
    def _action(action: str, reason: str, href: str) -> dict[str, str]:
        return {"action": action, "reason": reason, "href": href}

    def _recommended(
        self,
        dimensions: list[dict[str, Any]],
        counts: dict[str, int],
        workspace_id: str,
    ) -> dict[str, str]:
        """返回首个阻塞能力维度的首个操作，即下一步。

        ``research`` 维度刻意排除在此循环之外：“尚无已确认结果”不会阻塞流程，
        它只是能力维度就绪后展示的下一步。
        """
        for dim in dimensions:
            if dim["key"] == "research":
                continue
            if dim["ready"]:
                continue
            if dim["waiting"]:
                return {
                    "title": "查看处理进度",
                    "description": f"{dim['label']}：{dim['summary']}，后台任务还在运行。",
                    "href": f"/workspaces/{workspace_id}/activity",
                    "label": "打开处理中心",
                }
            if dim["blocking_actions"]:
                first = dim["blocking_actions"][0]
                return {
                    "title": first["action"],
                    "description": first["reason"],
                    "href": first["href"],
                    "label": first["action"],
                }
# 能力维度已就绪，继续推进 HITL 闭环。
        if counts["pending_knowledge"] > 0 and counts["confirmed_items"] == 0:
            return {
                "title": "审核确认知识",
                "description": f"已有 {counts['pending_knowledge']} 条待审核知识，确认后可作为更可信的发现输入。",
                "href": f"/workspaces/{workspace_id}/knowledge",
                "label": "打开知识工作台",
            }
        if counts["pending_opportunities"] > 0:
            return {
                "title": "处理待确认机会",
                "description": "各维度已就绪，先处理待人工确认的研究机会。",
                "href": f"/workspaces/{workspace_id}/discover",
                "label": "查看机会",
            }
        if counts["confirmed_opportunities"] == 0 and counts["research_plans"] == 0:
            return {
                "title": "运行 Discover 并确认机会",
                "description": "研究准备度已就绪，运行发现流程产生候选，再人工确认一个研究方向。",
                "href": f"/workspaces/{workspace_id}/discover",
                "label": "启动 Discover",
            }
        return {
            "title": "进入研究中心",
            "description": "研究准备度全部就绪，继续推进研究计划与代码生成。",
            "href": f"/workspaces/{workspace_id}/plans",
            "label": "打开研究中心",
        }
