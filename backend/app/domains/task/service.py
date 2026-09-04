"""带状态机的 Task service 层。

状态转换会根据显式矩阵进行校验。任何不在 ALLOWED_TRANSITIONS 中、试图将状态从 A
转换为 B 的操作，都会抛出 InvalidTaskTransition。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.task.models import Task
from app.domains.task.schemas import TaskCreate, TaskUpdate, summarize_task_error
from app.domains.timeline.service import TimelineService

logger = get_logger(__name__)


class TaskNotFoundError(Exception):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task not found: {task_id}")
        self.task_id = task_id


class InvalidTaskTransition(Exception):
    def __init__(self, from_status: str, to_status: str) -> None:
        super().__init__(f"Invalid task transition: {from_status} -> {to_status}")
        self.from_status = from_status
        self.to_status = to_status


# 允许的前向状态转换。键是当前状态，值是任务可以从该状态转换到的状态集合。
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
# 失败任务重试后可能发生排队失败（例如本地 worker/broker 不可用）。
# 此时必须回到 failed，不能让 UI 永远显示 queued。
    "queued": {"running", "failed", "cancel_requested", "cancelled"},
    "running": {
        "waiting_for_user",
        "succeeded",
        "failed",
        "cancel_requested",
        "cancelled",
    },
    "waiting_for_user": {"running", "cancel_requested", "cancelled", "failed"},
    "cancel_requested": {"cancelled", "running"},  # cancel can be preempted
    "succeeded": set(),  # terminal
    "failed": {"queued"},  # retry
    "cancelled": set(),  # terminal
}

TERMINAL_STATUSES = {"succeeded", "cancelled"}


class TaskService:
    """Task 生命周期管理。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.timeline_service = TimelineService(db)

# ------------------------------------------------------------ 创建
    def create(self, payload: TaskCreate) -> Task:
        task = Task(
            id=str(uuid4()),
            workspace_id=payload.workspace_id,
            task_type=payload.task_type,
            status="queued",
            progress=0.0,
            payload=dict(payload.payload),
            is_deleted=False,
        )
        self.db.add(task)
        self.db.commit()
        self.db.refresh(task)
        self._timeline(task, "task.created")
        logger.info("task.created", task_id=task.id, task_type=task.task_type)
        return task

# ----------------------------------------------------------------- 读取
    def get(self, task_id: str) -> Task:
        self._validate_uuid(task_id)
        t = self.db.get(Task, task_id)
        if t is None or t.is_deleted:
            raise TaskNotFoundError(task_id)
        return t

    def list(
        self,
        *,
        workspace_id: str | None = None,
        status_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Task], int]:
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        q = select(Task).where(Task.is_deleted.is_(False))
        if workspace_id is not None:
            q = q.where(Task.workspace_id == workspace_id)
        if status_filter is not None:
            q = q.where(Task.status == status_filter)
        items_q = q.order_by(Task.created_at.desc()).limit(limit).offset(offset)
        total_q = select(func.count()).select_from(q.subquery())
        items = list(self.db.execute(items_q).scalars().all())
        total = int(self.db.execute(total_q).scalar() or 0)
        return items, total

# ------------------------------------------------------- 状态机
    def transition(
        self,
        task_id: str,
        to_status: str,
        *,
        progress: float | None = None,
        result: dict | None = None,
        error: str | None = None,
        payload_patch: dict | None = None,
    ) -> Task:
        """校验状态转换并将任务移动到新状态。

        同时以原子方式更新 progress/result/error/payload 中传入的字段。
        """
        task = self.get(task_id)
        from_status = task.status
        if to_status != from_status and to_status not in ALLOWED_TRANSITIONS.get(
            from_status, set()
        ):
            raise InvalidTaskTransition(from_status, to_status)

        task.status = to_status
        if progress is not None:
            task.progress = max(0.0, min(1.0, float(progress)))
        if result is not None:
            task.result = result
        if error is not None:
            task.error = error
        if payload_patch is not None:
            merged = dict(task.payload or {})
            merged.update(payload_patch)
            task.payload = merged

        self.db.commit()
        self.db.refresh(task)
        self._timeline(
            task,
            f"task.{to_status}",
            extra={
                "from_status": from_status,
                "progress": task.progress,
                "error": task.error,
            },
        )
        logger.info(
            "task.transition",
            task_id=task.id,
            from_status=from_status,
            to_status=to_status,
        )
        return task

# --------------------------------------------------------- 用户操作
    def request_cancel(self, task_id: str) -> Task:
        """面向用户的取消操作。撤销运行中的 celery 任务并完成
        并立即完成取消。

        之前的行为停留在 ``cancel_requested``，但没有 poller / worker 会推进该状态，
        导致 UI 永远卡在“正在取消”。这里直接完成到 ``cancelled``（并尽力执行 celery
        revoke），使取消行为可确定。
        """
        task = self.get(task_id)
        if task.status in TERMINAL_STATUSES:
            raise InvalidTaskTransition(task.status, "cancelled")
        if task.celery_task_id:
            try:
                from app.workers.celery_app import celery_app

                celery_app.control.revoke(task.celery_task_id)
            except Exception:
                pass  # best effort — the DB state below is authoritative
        return self.transition(task_id, "cancelled")

    def resume_from_user(self, task_id: str, *, decision: dict | None = None) -> Task:
        """用户恢复一个等待输入的任务。"""
        task = self.get(task_id)
        if task.status != "waiting_for_user":
            raise InvalidTaskTransition(task.status, "running")
        return self.transition(
            task_id,
            "running",
            payload_patch={"user_decision": decision} if decision else None,
        )

    def retry(self, task_id: str) -> Task:
        """重新排队一个失败任务。

        清除之前的错误和进度，切换到 ``queued``，然后重新派发底层 celery task，确保
        队列中的任务行真正被处理（此前它只停留在 ``queued``，没有重新入队）。
        """
        task = self.get(task_id)
        if task.status != "failed":
            raise InvalidTaskTransition(task.status, "queued")
# 重新排队前清除错误/进度，使重试从干净状态开始。
        task.error = None
        task.progress = 0.0
        self.db.commit()
        self.db.refresh(task)
        transited = self.transition(task_id, "queued")
        from app.workers.tasks.dispatch import redispatch_task

        try:
            celery_task_id = redispatch_task(task)
        except Exception as exc:
            return self.transition(
                task_id,
                "failed",
                error=f"任务重新派发失败，请确认本地 Worker 与 Redis 正在运行后重试：{exc}",
            )
        if not celery_task_id:
            return self.transition(
                task_id,
                "failed",
                error="此任务暂不支持重新派发，请从对应功能页面重新发起。",
            )
        task.celery_task_id = celery_task_id
        self.db.commit()
        return transited

# ----------------------------------------------------------------- 更新
    def update_progress(self, task_id: str, progress: float) -> Task:
        """更新进度而不改变状态（仅运行中允许）。"""
        task = self.get(task_id)
        if task.status != "running":
            raise InvalidTaskTransition(task.status, "running")
        task.progress = max(0.0, min(1.0, float(progress)))
        self.db.commit()
        self.db.refresh(task)
        return task

# ------------------------------------------------------------- 辅助函数
    def _timeline(self, task: Task, event_type: str, *, extra: dict | None = None) -> None:
        if not task.workspace_id:
            return  # workspace-scoped timeline only
        payload = {"task_type": task.task_type, "status": task.status}
        if extra:
            payload.update(extra)
        if "error" in payload:
            payload["error"] = summarize_task_error(payload["error"])
        self.timeline_service.record(
            workspace_id=task.workspace_id,
            event_type=event_type,
            subject_type="task",
            subject_id=task.id,
            payload=payload,
        )

    @staticmethod
    def _validate_uuid(value: str) -> None:
        try:
            UUID(str(value))
        except (ValueError, TypeError) as e:
            raise TaskNotFoundError(value) from e
