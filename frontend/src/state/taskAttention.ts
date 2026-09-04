import type { Task } from "../api/types/domain";

/**
* 失败任务永久保留在审计轨迹中，但概览只应提醒仍需及时处理的失败。
 */
export const RECENT_FAILED_TASK_WINDOW_MS = 24 * 60 * 60 * 1000;

export function isTaskNeedingAttention(
  task: Pick<Task, "status" | "created_at" | "updated_at">,
  now = Date.now(),
): boolean {
  if (["queued", "running", "waiting_for_user"].includes(task.status)) {
    return true;
  }
  if (task.status !== "failed") return false;

  const updatedAt = Date.parse(task.updated_at || task.created_at);
// 时间戳格式错误时应安全处理：保持失败可见，直到它被检查，
// 不要静默隐藏未知的近期问题。
  return Number.isNaN(updatedAt) || now - updatedAt <= RECENT_FAILED_TASK_WINDOW_MS;
}
