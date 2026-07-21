import apiClient from "./client";
import type {
  Workspace,
  WorkspaceCreate,
  WorkspaceListParams,
  WorkspaceListResponse,
  WorkspaceUpdate,
} from "./types/workspace";

export const workspaceApi = {
  async list(params: WorkspaceListParams = {}): Promise<WorkspaceListResponse> {
    const resp = await apiClient.get<WorkspaceListResponse>("/workspaces", {
      params: {
        include_archived: params.include_archived ?? false,
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      },
    });
    return resp.data;
  },

  async get(id: string): Promise<Workspace> {
    const resp = await apiClient.get<Workspace>(`/workspaces/${id}`);
    return resp.data;
  },

  async create(payload: WorkspaceCreate): Promise<Workspace> {
    const resp = await apiClient.post<Workspace>("/workspaces", payload);
    return resp.data;
  },

  async update(id: string, payload: WorkspaceUpdate): Promise<Workspace> {
    const resp = await apiClient.patch<Workspace>(`/workspaces/${id}`, payload);
    return resp.data;
  },

  async archive(id: string): Promise<Workspace> {
    const resp = await apiClient.post<Workspace>(`/workspaces/${id}/archive`);
    return resp.data;
  },

  async unarchive(id: string): Promise<Workspace> {
    const resp = await apiClient.post<Workspace>(`/workspaces/${id}/unarchive`);
    return resp.data;
  },

  async remove(id: string): Promise<{ id: string; deleted: boolean }> {
    const resp = await apiClient.delete<{ id: string; deleted: boolean }>(
      `/workspaces/${id}`
    );
    return resp.data;
  },
};

export default workspaceApi;
