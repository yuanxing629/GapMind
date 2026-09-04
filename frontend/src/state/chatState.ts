import axios from "axios";
import type { ChatConversation, ChatMessage } from "../api/chat";
import { requestErrorMessage } from "./requestFeedback";

export function truncateChatTitle(title: string, maxLength = 38): string {
  const normalized = title.replace(/\s+/g, " ").trim();
  return normalized.length > maxLength ? `${normalized.slice(0, maxLength)}…` : normalized;
}

export function sortChatMessages(messages: ChatMessage[]): ChatMessage[] {
  return [...messages].sort((a, b) => a.sequence - b.sequence);
}

export function chatConversationPath(
  conversation: ChatConversation,
  independentWorkspaceIds: ReadonlySet<string> = new Set(),
): string {
  return conversation.workspace_id && !independentWorkspaceIds.has(conversation.workspace_id)
    ? `/workspaces/${conversation.workspace_id}/assistant/${conversation.id}`
    : `/chat/${conversation.id}`;
}

export function shouldSendOnEnter(event: { key: string; shiftKey: boolean; nativeEvent?: { isComposing?: boolean } }): boolean {
  return event.key === "Enter" && !event.shiftKey && !event.nativeEvent?.isComposing;
}

export function conversationGroupLabel(dateValue: string | null): "今天" | "最近 7 天" | "更早" {
  if (!dateValue) return "更早";
  const date = new Date(dateValue);
  const now = new Date();
  const startToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const timestamp = date.getTime();
  if (timestamp >= startToday) return "今天";
  if (timestamp >= startToday - 6 * 24 * 60 * 60 * 1000) return "最近 7 天";
  return "更早";
}

export function groupConversations(conversations: ChatConversation[]): Array<{ label: string; items: ChatConversation[] }> {
  const groups = new Map<string, ChatConversation[]>();
  for (const conversation of conversations) {
    const label = conversationGroupLabel(conversation.last_message_at ?? conversation.updated_at);
    groups.set(label, [...(groups.get(label) ?? []), conversation]);
  }
  return ["今天", "最近 7 天", "更早"]
    .map((label) => ({ label, items: groups.get(label) ?? [] }))
    .filter((group) => group.items.length > 0);
}

export function chatErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "object" && detail?.message) return String(detail.message);
    if (error.response?.status === 404) return "这个对话不存在，可能已经被删除。";
    if (error.response?.status === 409) return "当前对话正在生成回答，请稍候再发送。";
    if (error.response?.status === 503) return "AI 服务尚未配置，请联系管理员。";
    if (error.response?.status === 502) return "AI 服务暂时不可用，请稍后重试。";
  }
  return requestErrorMessage(error);
}

export type ChatRetrievalDiagnosticCode =
  | "embedding_unavailable"
  | "milvus_unavailable"
  | "collection_unloaded"
  | "reranker_degraded"
  | "unknown";

const RETRIEVAL_DIAGNOSTIC_COPY: Record<ChatRetrievalDiagnosticCode, { title: string; recovery: string }> = {
  embedding_unavailable: {
    title: "无法生成论文检索向量",
    recovery: "请检查 embedding API Key、服务地址和网络后重试。",
  },
  milvus_unavailable: {
    title: "无法连接工作区向量库",
    recovery: "请检查 Milvus、etcd 和 minio 基础设施状态后重试。",
  },
  collection_unloaded: {
    title: "论文向量集合尚未加载",
    recovery: "请重新加载 collection 后重试；当前不需要直接重建索引。",
  },
  reranker_degraded: {
    title: "重排服务暂时不可用，已降级为向量召回",
    recovery: "当前结果仍可查看；恢复 reranker 服务后可重新尝试。",
  },
  unknown: {
    title: "工作区论文检索遇到未分类故障",
    recovery: "请稍后重试；若持续发生，请查看后端诊断日志。",
  },
};

export function retrievalDiagnosticCopy(code?: string | null): { title: string; recovery: string } | null {
  if (!code || !(code in RETRIEVAL_DIAGNOSTIC_COPY)) return null;
  return RETRIEVAL_DIAGNOSTIC_COPY[code as ChatRetrievalDiagnosticCode];
}

/**
* 失败消息会持久化，因此重新加载时必须保留修复文案，
* 同时不将原始上游错误泄露到研究 workspace UI。
 */
export function chatFailureMessage(
  message: Pick<ChatMessage, "grounding_status" | "error_message" | "retrieval_diagnostic_code">,
): string {
  if (message.grounding_status === "retrieval_failed") {
    const diagnostic = retrievalDiagnosticCopy(message.retrieval_diagnostic_code);
    if (diagnostic) return `${diagnostic.title} ${diagnostic.recovery}`;
    return "工作区论文检索暂不可用，请检查向量化服务与 Milvus 后重试。";
  }
  if (message.error_message?.includes("流式响应中断")) {
    return "生成过程意外中断，请重新尝试。";
  }
  if (message.error_message?.includes("API key is not configured")) {
    return "AI 服务尚未配置，请联系管理员。";
  }
  return "回答失败，请重试。";
}
