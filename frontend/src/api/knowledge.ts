import apiClient from "./client";
import type { ExtractionRejectionListResponse } from "./types/domain";
import type {
  EvidenceSpanListResponse,
  EvidenceContext,
  KnowledgeGraphResponse,
  KnowledgeGraphSearchResponse,
  KnowledgeItemListResponse,
  KnowledgeRelationListResponse,
} from "./types/knowledge";

export interface KnowledgeListParams {
  type?: string;
  status?: string;
  paper_id?: string;
  q?: string;
  min_confidence?: number;
  limit?: number;
  offset?: number;
}

export interface KnowledgeGraphParams {
  type?: string;
  paper_id?: string;
  q?: string;
  min_confidence?: number;
  relation_type?: string;
  limit?: number;
  offset?: number;
  status?: string;
  edge_limit?: number;
  include_related_papers?: boolean;
  projection_mode?: "all" | "workspace" | "landscape" | "claims" | "evidence";
}

export const knowledgeApi = {
  async listItems(
    workspaceId: string,
    params: KnowledgeListParams = {},
  ): Promise<KnowledgeItemListResponse> {
    const resp = await apiClient.get<KnowledgeItemListResponse>(
      `/workspaces/${workspaceId}/knowledge`,
      { params: { limit: 200, offset: 0, ...params } },
    );
    return resp.data;
  },

  async listEvidence(
    workspaceId: string,
    itemId: string,
  ): Promise<EvidenceSpanListResponse> {
    const resp = await apiClient.get<EvidenceSpanListResponse>(
      `/workspaces/${workspaceId}/knowledge/${itemId}/evidence`,
    );
    return resp.data;
  },

  async listRelations(
    workspaceId: string,
    params: { item_id?: string; relation_type?: string } = {},
  ): Promise<KnowledgeRelationListResponse> {
    const resp = await apiClient.get<KnowledgeRelationListResponse>(
      `/workspaces/${workspaceId}/knowledge/relations`,
      { params: { limit: 500, offset: 0, ...params } },
    );
    return resp.data;
  },

  async graph(
    workspaceId: string,
    params: KnowledgeGraphParams = {},
  ): Promise<KnowledgeGraphResponse> {
    const resp = await apiClient.get<KnowledgeGraphResponse>(
      `/workspaces/${workspaceId}/knowledge/graph`,
      { params: { limit: 100, offset: 0, ...params } },
    );
    return resp.data;
  },

  async graphNeighbors(
    workspaceId: string,
    nodeId: string,
    params: {
      depth?: number;
      limit?: number;
      relation_type?: string;
      projection_mode?: KnowledgeGraphParams["projection_mode"];
    } = {},
  ): Promise<KnowledgeGraphResponse> {
    const resp = await apiClient.get<KnowledgeGraphResponse>(
      `/workspaces/${workspaceId}/knowledge/graph/neighbors/${encodeURIComponent(nodeId)}`,
      { params: { depth: 1, limit: 100, ...params } },
    );
    return resp.data;
  },

  async searchGraphNodes(
    workspaceId: string,
    params: { q: string; projection_mode?: KnowledgeGraphParams["projection_mode"]; limit?: number },
  ): Promise<KnowledgeGraphSearchResponse> {
    const resp = await apiClient.get<KnowledgeGraphSearchResponse>(
      `/workspaces/${workspaceId}/knowledge/graph/search`,
      { params },
    );
    return resp.data;
  },

  async reviewItem(
    workspaceId: string,
    itemId: string,
    payload: {
      action: "confirm" | "edit" | "reject";
      canonical_name?: string;
      content?: Record<string, unknown>;
      confidence?: number;
      note?: string;
    },
  ): Promise<import("./types/knowledge").KnowledgeItem> {
    const resp = await apiClient.patch<import("./types/knowledge").KnowledgeItem>(
      `/workspaces/${workspaceId}/knowledge/${itemId}/review`,
      payload,
    );
    return resp.data;
  },

  async evidenceContext(workspaceId: string, itemId: string): Promise<EvidenceContext> {
    const resp = await apiClient.get<EvidenceContext>(
      `/workspaces/${workspaceId}/knowledge/${itemId}/evidence/context`,
    );
    return resp.data;
  },

  async listExtractionRejections(
    workspaceId: string,
    runId: string,
    params: {
      kind?: string;
      stage?: string;
      reason_code?: string;
      limit?: number;
      offset?: number;
    } = {},
  ): Promise<ExtractionRejectionListResponse> {
    const resp = await apiClient.get<ExtractionRejectionListResponse>(
      `/workspaces/${workspaceId}/extraction-runs/${runId}/rejections`,
      { params: { limit: 50, offset: 0, ...params } },
    );
    return resp.data;
  },
};

export default knowledgeApi;
