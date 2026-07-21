import apiClient from "./client";
import type { Task, TaskListResponse } from "./types/domain";

export const taskApi = {
  async list(
    workspaceId: string,
    params: { status?: string; limit?: number; offset?: number } = {}
  ): Promise<TaskListResponse> {
    const resp = await apiClient.get<TaskListResponse>(
      `/workspaces/${workspaceId}/tasks`,
      {
        params: {
          status: params.status,
          limit: params.limit ?? 100,
          offset: params.offset ?? 0,
        },
      }
    );
    return resp.data;
  },

  async get(taskId: string): Promise<Task> {
    const resp = await apiClient.get<Task>(`/tasks/${taskId}`);
    return resp.data;
  },

  async cancel(taskId: string): Promise<Task> {
    const resp = await apiClient.post<Task>(`/tasks/${taskId}/cancel`);
    return resp.data;
  },

  async retry(taskId: string): Promise<Task> {
    const resp = await apiClient.post<Task>(`/tasks/${taskId}/retry`);
    return resp.data;
  },

  async resume(taskId: string, decision?: Record<string, unknown>): Promise<Task> {
    const resp = await apiClient.post<Task>(`/tasks/${taskId}/resume`, {
      decision: decision ?? null,
    });
    return resp.data;
  },
};

export default taskApi;
