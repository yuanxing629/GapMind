"""为重新排队的 Task 行重新派发 Celery 任务。

``TaskService.retry`` 会在数据库中将失败 Task 转换为 ``queued``，但只有重新将
对应 Celery 任务加入队列后，该行才会真正被处理。本模块将 ``task_type`` 映射
回对应 Celery 任务，使 retry 可以重新派发。模块在 ``TaskService.retry`` 内延迟
导入，以避免 task-domain -> workers 的导入循环。
"""

from __future__ import annotations

from typing import Any


def redispatch_task(task: Any) -> str | None:
    """将与重新排队的 ``Task`` 行匹配的 Celery 任务加入队列。

    返回新的 celery task id。对于没有对应 celery task 的 task type（例如历史数据行）返回
    ``None``，此时该行保持 ``queued``。
    """
    if task.task_type == "parse_pdf":
        from app.workers.tasks.parse_pdf import parse_pdf_task

        return str(parse_pdf_task.delay(task.id).id)
    if task.task_type == "extract_knowledge":
        from app.workers.tasks.extract_knowledge import extract_knowledge_task

        return str(extract_knowledge_task.delay(task.id).id)
    if task.task_type == "embed_chunks":
        from app.workers.tasks.embed_chunks import embed_chunks_task

        return str(embed_chunks_task.delay(task.id).id)
    if task.task_type == "extract_gap_annotation":
        from app.workers.tasks.extract_gap_annotation import extract_gap_annotation_task

        return str(extract_gap_annotation_task.delay(task.id).id)
    if task.task_type == "discover_agent":
# Discover agent 任务接收 run id，而不是 task id。
        run_id = (task.payload or {}).get("run_id")
        if run_id:
            from app.workers.tasks.run_agent import run_agent_task

            return str(run_agent_task.delay(run_id).id)
    return None
