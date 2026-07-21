import apiClient from "./client";
import type {
  Paper,
  PaperCreate,
  PaperListResponse,
  PaperUpdate,
} from "./types/domain";

export interface PaperUploadParams {
  filename: string;
  content: Blob;
  mime_type?: string;
  title?: string;
  authors?: string[];
  year?: number;
  abstract?: string;
  doi?: string;
  arxiv_id?: string;
}

export const paperApi = {
  async list(workspaceId: string, params: { limit?: number; offset?: number } = {}): Promise<PaperListResponse> {
    const resp = await apiClient.get<PaperListResponse>(
      `/workspaces/${workspaceId}/papers`,
      { params: { limit: params.limit ?? 50, offset: params.offset ?? 0 } }
    );
    return resp.data;
  },

  async get(workspaceId: string, paperId: string): Promise<Paper> {
    const resp = await apiClient.get<Paper>(`/workspaces/${workspaceId}/papers/${paperId}`);
    return resp.data;
  },

  async create(workspaceId: string, payload: PaperCreate): Promise<Paper> {
    const resp = await apiClient.post<Paper>(`/workspaces/${workspaceId}/papers`, payload);
    return resp.data;
  },

  async upload(workspaceId: string, params: PaperUploadParams): Promise<Paper> {
    const form = new FormData();
    form.append("file", params.content, params.filename);
    if (params.title) form.append("title", params.title);
    if (params.authors && params.authors.length)
      form.append("authors", params.authors.join(", "));
    if (params.year) form.append("year", String(params.year));
    if (params.abstract) form.append("abstract", params.abstract);
    if (params.doi) form.append("doi", params.doi);
    if (params.arxiv_id) form.append("arxiv_id", params.arxiv_id);

    const resp = await apiClient.post<Paper>(
      `/workspaces/${workspaceId}/papers/upload`,
      form,
      { headers: { "Content-Type": "multipart/form-data" } }
    );
    return resp.data;
  },

  async update(workspaceId: string, paperId: string, payload: PaperUpdate): Promise<Paper> {
    const resp = await apiClient.patch<Paper>(
      `/workspaces/${workspaceId}/papers/${paperId}`,
      payload
    );
    return resp.data;
  },

  async attachPdf(
    workspaceId: string,
    paperId: string,
    params: { filename: string; content: Blob; mime_type?: string }
  ): Promise<Paper> {
    const form = new FormData();
    form.append("file", params.content, params.filename);
    const resp = await apiClient.post<Paper>(
      `/workspaces/${workspaceId}/papers/${paperId}/upload-pdf`,
      form,
      { headers: { "Content-Type": "multipart/form-data" } }
    );
    return resp.data;
  },

  async remove(workspaceId: string, paperId: string): Promise<{ id: string; deleted: boolean }> {
    const resp = await apiClient.delete<{ id: string; deleted: boolean }>(
      `/workspaces/${workspaceId}/papers/${paperId}`
    );
    return resp.data;
  },
};

export default paperApi;
