import apiClient from "./client";

export interface GapExtractionTask {
  paper_id: string;
  task_id: string;
  status: string;
  skipped?: boolean;
  input_mode?: string | null;
  knowledge_extraction_run_id?: string | null;
  dependency_status?: string;
}

export interface GapAnnotation {
  id: string;
  paper_id: string;
  status: string;
  attempts: number;
  model_provider: string;
  model_name: string;
  model_parameters?: Record<string, unknown>;
  knowledge_extraction_run_id?: string | null;
  knowledge_context_sha256?: string | null;
  input_mode?: string;
  source_knowledge_item_ids?: string[];
  source_evidence_span_ids?: string[];
  context_char_count?: number;
  context_fallback_reason?: string | null;
  validation_errors: string[];
  fallback_reason?: string | null;
  stale?: boolean;
  updated_at: string;
}

export interface GapBoardAxis {
  concept_id: string;
  label: string;
  aliases: string[];
  paper_count: number;
  paper_ids: string[];
}

export interface GapBoardCell {
  method_concept_id: string;
  problem_concept_id: string;
  addressed: boolean;
  addressed_paper_ids: string[];
  limitation_paper_ids: string[];
  cooccurrence_paper_ids: string[];
  explicit_limitation: boolean;
  candidate_score: number;
  candidate_tier: "covered" | "explicit_limitation" | "same_paper_unlinked" | "cross_paper_transfer" | "corpus_only";
  candidate_reasons: string[];
  eligible_for_discovery: boolean;
  verification_status: string;
}

export interface GapBoard {
  id: string;
  workspace_id: string;
  version: number;
  filters: Record<string, unknown>;
  method_axes: GapBoardAxis[];
  problem_axes: GapBoardAxis[];
  cells: GapBoardCell[];
  source_annotation_ids: string[];
  candidate_count: number;
  created_at: string;
}

export const gapApi = {
  async extract(
    workspaceId: string,
    paperIds: string[],
    force = false,
  ): Promise<{ tasks: GapExtractionTask[] }> {
    return (
      await apiClient.post(`/workspaces/${workspaceId}/gap/extractions`, {
        paper_ids: paperIds,
        force,
      })
    ).data;
  },

  async listAnnotations(
    workspaceId: string,
  ): Promise<{ items: GapAnnotation[]; total: number }> {
    return (await apiClient.get(`/workspaces/${workspaceId}/gap/annotations`)).data;
  },

  async rebuildBoard(workspaceId: string, paperIds: string[] = []): Promise<GapBoard> {
    return (
      await apiClient.post(`/workspaces/${workspaceId}/gap/board/rebuild`, {
        paper_ids: paperIds,
      })
    ).data;
  },

  async getBoard(workspaceId: string): Promise<GapBoard> {
    return (await apiClient.get(`/workspaces/${workspaceId}/gap/board`)).data;
  },

  async discoverCandidate(
    workspaceId: string,
    methodConceptId: string,
    problemConceptId: string,
    exploratory = false,
  ): Promise<{ run_id: string; task_id?: string; status: string }> {
    return (
      await apiClient.post(`/workspaces/${workspaceId}/gap/candidates/discover`, {
        method_concept_id: methodConceptId,
        problem_concept_id: problemConceptId,
        max_opportunities: 3,
        exploratory,
      })
    ).data;
  },
};

export default gapApi;
