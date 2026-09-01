import apiClient, { apiBaseURL, getCsrfToken } from "./client";

export interface ChatConversation {
  id: string;
  title: string;
  workspace_id?: string | null;
  model: string | null;
  last_message_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  status: "completed" | "generating" | "failed";
  error_message: string | null;
  sequence: number;
  model: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  prompt_chars: number | null;
  response_chars: number | null;
  first_token_latency_ms: number | null;
  completion_latency_ms: number | null;
  grounding_status?: "not_requested" | "grounded" | "plan_context" | "context_selection_required" | "no_evidence" | "retrieval_failed";
  retrieval_diagnostic_code?: "embedding_unavailable" | "milvus_unavailable" | "collection_unloaded" | "reranker_degraded" | "unknown" | null;
  citation_quality?: CitationQuality;
  retrieval_audit?: RetrievalAudit;
  citations?: ChatMessageEvidence[];
  citation_check?: CitationCheck | null;
  sources?: ChatMessageSource[];
  source_check?: SourceCheck | null;
  created_at: string;
  updated_at: string;
}

export interface CitationCheck {
  referenced: number[];
  broken: number[];
  ok: boolean;
  grounded_without_citations: boolean;
}

export interface CitationQuality {
  status: "not_needed" | "passed" | "repaired" | "rejected";
  attempts: number;
  initial_broken_citations: number[];
  initial_grounded_without_citations: boolean;
  initial_broken_sources: string[];
  final_broken_citations: number[];
  final_grounded_without_citations: boolean;
  final_broken_sources: string[];
  fallback: boolean;
}

export interface RetrievalAudit {
  request_id: string;
  status: string;
  diagnostic_code: string | null;
  recall_count: number | null;
  returned_chunk_count: number;
  final_paper_count: number;
  latency_ms: number;
  reranker_status: "applied" | "enabled_no_rerank" | "degraded" | "disabled" | "unknown";
}

export interface SourceCheck {
  referenced: string[];
  broken: string[];
  ok: boolean;
}

export type ChatSourceType = "plan" | "paper" | "report" | "code_draft";

export interface ChatMessageSource {
  marker: string;
  source_type: ChatSourceType;
  source_id: string;
  label: string;
  title: string;
  status: string;
  detail: string | null;
}

export interface ChatContextPlanOption {
  id: string;
  title: string;
  research_question: string;
  status: string;
}

export interface ChatContextArtifactOption {
  id: string;
  plan_id: string;
  source_type: "report" | "code_draft";
  label: string;
  title: string;
  status: string;
}

export interface ChatContextOptionsResponse {
  plans: ChatContextPlanOption[];
  artifacts: ChatContextArtifactOption[];
}

export interface ChatMessageEvidence {
  id: string;
  message_id: string;
  workspace_id: string;
  paper_id: string | null;
  artifact_id: string | null;
  chunk_id: string | null;
  paper_title: string | null;
  section: string | null;
  excerpt: string;
  start_char: number | null;
  end_char: number | null;
  score: number;
  rank: number;
  created_at: string;
  updated_at: string;
}

export interface ChatEvidenceContext {
  evidence: ChatMessageEvidence;
  available: boolean;
  artifact_kind: string | null;
  filename: string | null;
  content: string | null;
  message: string | null;
}

export interface ChatConversationListResponse {
  items: ChatConversation[];
  total: number;
  limit: number;
  offset: number;
}

export interface ChatConversationDetail {
  conversation: ChatConversation;
  messages: ChatMessage[];
}

export interface ChatSendResponse {
  conversation: ChatConversation;
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}

export const chatApi = {
  async listConversations(params: { query?: string; workspace_id?: string; limit?: number; offset?: number } = {}) {
    const { data } = await apiClient.get<ChatConversationListResponse>("/chat/conversations", { params });
    return data;
  },
  async listContextOptions(workspaceId: string) {
    const { data } = await apiClient.get<ChatContextOptionsResponse>("/chat/context-options", { params: { workspace_id: workspaceId } });
    return data;
  },
  async getConversation(id: string) {
    const { data } = await apiClient.get<ChatConversationDetail>(`/chat/conversations/${id}`);
    return data;
  },
  async createConversation(title?: string, workspaceId?: string) {
    const { data } = await apiClient.post<ChatConversation>("/chat/conversations", {
      title: title ?? null,
      workspace_id: workspaceId ?? null,
    });
    return data;
  },
  async renameConversation(id: string, title: string) {
    const { data } = await apiClient.patch<ChatConversation>(`/chat/conversations/${id}`, { title });
    return data;
  },
  async deleteConversation(id: string) {
    const { data } = await apiClient.delete<{ id: string; deleted: boolean }>(`/chat/conversations/${id}`);
    return data;
  },
  async sendNew(content: string, workspaceId?: string, context: { researchPlanId?: string; sourceArtifactIds?: string[] } = {}) {
    const { data } = await apiClient.post<ChatSendResponse>("/chat/conversations/send", {
      content,
      workspace_id: workspaceId ?? null,
      research_plan_id: context.researchPlanId ?? null,
      source_artifact_ids: context.sourceArtifactIds ?? [],
    });
    return data;
  },
  async sendMessage(id: string, content: string, context: { researchPlanId?: string; sourceArtifactIds?: string[] } = {}) {
    const { data } = await apiClient.post<ChatSendResponse>(`/chat/conversations/${id}/messages`, {
      content,
      research_plan_id: context.researchPlanId ?? null,
      source_artifact_ids: context.sourceArtifactIds ?? [],
    });
    return data;
  },
  async streamSend(conversationId: string, content: string, context: { researchPlanId?: string; sourceArtifactIds?: string[] } = {}): Promise<Response> {
    // SSE uses the same API base as Axios so a separately hosted frontend can
    // carry the session cookie with an explicit CORS configuration.
    const csrf = getCsrfToken();
    return fetch(`${apiBaseURL.replace(/\/$/, "")}/chat/conversations/${conversationId}/messages/stream`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...(csrf ? { "X-CSRF-Token": csrf } : {}),
      },
      body: JSON.stringify({
        content,
        research_plan_id: context.researchPlanId ?? null,
        source_artifact_ids: context.sourceArtifactIds ?? [],
      }),
    });
  },
  async retryMessage(conversationId: string, assistantMessageId: string) {
    const { data } = await apiClient.post<ChatSendResponse>(
      `/chat/conversations/${conversationId}/messages/${assistantMessageId}/retry`,
    );
    return data;
  },
  async getEvidenceContext(conversationId: string, messageId: string, evidenceId: string) {
    const { data } = await apiClient.get<ChatEvidenceContext>(
      `/chat/conversations/${conversationId}/messages/${messageId}/evidence/${evidenceId}/context`,
    );
    return data;
  },
};

export default chatApi;
