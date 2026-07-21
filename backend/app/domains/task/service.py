"""Task service layer with state machine.

State transitions are validated against an explicit matrix. Any attempt to
move from status A to status B that's not in ALLOWED_TRANSITIONS raises
InvalidTaskTransition.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.domains.task.models import Task
from app.domains.task.schemas import TaskCreate, TaskUpdate
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


# Allowed forward transitions. Keys are the current status, values are the
# set of statuses the task may move INTO from that status.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "queued": {"running", "cancel_requested", "cancelled"},
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
    """Task lifecycle management."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.timeline_service = TimelineService(db)

    # ------------------------------------------------------------ create
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

    # ----------------------------------------------------------------- read
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

    # ------------------------------------------------------- state machine
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
        """Move a task to a new status, validating the transition.

        Also patches any of progress/result/error/payload atomically.
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

    # --------------------------------------------------------- user actions
    def request_cancel(self, task_id: str) -> Task:
        """User-facing cancel request. Only valid from non-terminal states."""
        task = self.get(task_id)
        if task.status in TERMINAL_STATUSES:
            raise InvalidTaskTransition(task.status, "cancel_requested")
        return self.transition(task_id, "cancel_requested")

    def resume_from_user(self, task_id: str, *, decision: dict | None = None) -> Task:
        """User resumes a task waiting for their input."""
        task = self.get(task_id)
        if task.status != "waiting_for_user":
            raise InvalidTaskTransition(task.status, "running")
        return self.transition(
            task_id,
            "running",
            payload_patch={"user_decision": decision} if decision else None,
        )

    def retry(self, task_id: str) -> Task:
        """Re-queue a failed task. Clears any prior error and progress."""
        task = self.get(task_id)
        if task.status != "failed":
            raise InvalidTaskTransition(task.status, "queued")
        # Clear error/progress before re-queuing so the retry starts clean.
        task.error = None
        task.progress = 0.0
        self.db.commit()
        self.db.refresh(task)
        return self.transition(task_id, "queued")

    # ----------------------------------------------------------------- update
    def update_progress(self, task_id: str, progress: float) -> Task:
        """Update progress without changing status (allowed only when running)."""
        task = self.get(task_id)
        if task.status != "running":
            raise InvalidTaskTransition(task.status, "running")
        task.progress = max(0.0, min(1.0, float(progress)))
        self.db.commit()
        self.db.refresh(task)
        return task

    # ------------------------------------------------------------- helpers
    def _timeline(self, task: Task, event_type: str, *, extra: dict | None = None) -> None:
        if not task.workspace_id:
            return  # workspace-scoped timeline only
        payload = {"task_type": task.task_type, "status": task.status}
        if extra:
            payload.update(extra)
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
