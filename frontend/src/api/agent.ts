import apiClient from "./client";

export type AgentType = "research_plan" | "code_generation" | "analyze" | "write" | "respond" | "deep_research";

export interface AgentStep {
  id: string;
  run_id: string;
  sequence: number;
  stage: string;
  status: string;
  summary: string;
  details: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface AgentArtifact {
  id: string;
  run_id: string;
  artifact_type: string;
  filename: string;
  mime_type: string;
  content: string;
  metadata: Record<string, unknown>;
  validation_status: string;
  created_at: string;
  updated_at: string;
}

export interface AgentRun {
  id: string;
  workspace_id: string;
  conversation_id: string | null;
  trigger_message_id: string | null;
  assistant_message_id: string | null;
  task_id: string | null;
  parent_run_id: string | null;
  agent_type: AgentType | string;
  status: string;
  current_stage: string;
  progress: number;
  input_payload: Record<string, unknown>;
  context_snapshot: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  requires_confirmation: boolean;
  created_at: string;
  updated_at: string;
}

export interface AgentRunDetail extends AgentRun {
  steps: AgentStep[];
  artifacts: AgentArtifact[];
}

export const agentApi = {
  async start(workspaceId: string, payload: { agent_type: AgentType; prompt: string; conversation_id: string; input?: Record<string, unknown> }) {
    const { data } = await apiClient.post<AgentRun>(`/workspaces/${workspaceId}/agent-runs`, {
      ...payload,
      input: payload.input ?? {},
    });
    return data;
  },
  async list(workspaceId: string, params: { conversation_id?: string; limit?: number; offset?: number } = {}) {
    const { data } = await apiClient.get<{ items: AgentRun[]; total: number }>(`/workspaces/${workspaceId}/agent-runs`, { params });
    return data;
  },
  async get(workspaceId: string, runId: string) {
    const { data } = await apiClient.get<AgentRunDetail>(`/workspaces/${workspaceId}/agent-runs/${runId}`);
    return data;
  },
  async cancel(workspaceId: string, runId: string) {
    const { data } = await apiClient.post<AgentRun>(`/workspaces/${workspaceId}/agent-runs/${runId}/cancel`);
    return data;
  },
  async confirm(workspaceId: string, runId: string) {
    const { data } = await apiClient.post<{ run: AgentRunDetail; research_plan_id: string | null }>(`/workspaces/${workspaceId}/agent-runs/${runId}/confirm`);
    return data;
  },
  async downloadArtifact(workspaceId: string, runId: string, artifactId: string) {
    const response = await apiClient.get<Blob>(`/workspaces/${workspaceId}/agent-runs/${runId}/artifacts/${artifactId}`, { responseType: "blob" });
    const url = URL.createObjectURL(response.data);
    const link = document.createElement("a");
    link.href = url;
// server 发送 X-File-Name（URL 编码，RFC 5987）；先解析，否则回退
    const headers = response.headers as unknown as Record<string, string>;
    const rawName = headers["x-file-name"] ?? "";
    link.download = rawName ? decodeURIComponent(rawName) : "artifact";
    link.rel = "noopener";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
  async downloadBundle(workspaceId: string, runId: string) {
    const { data } = await apiClient.get<Blob>(`/workspaces/${workspaceId}/agent-runs/${runId}/bundle`, { responseType: "blob" });
    const url = URL.createObjectURL(data);
    const link = document.createElement("a");
    link.href = url;
    link.download = `gapmind-agent-${runId.slice(0, 8)}.zip`;
    link.click();
    URL.revokeObjectURL(url);
  },
};

export default agentApi;

