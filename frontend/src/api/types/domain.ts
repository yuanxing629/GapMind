export interface Paper {
  id: string;
  workspace_id: string;
  primary_artifact_id: string | null;
  title: string;
  authors: string[];
  year: number | null;
  abstract: string | null;
  doi: string | null;
  arxiv_id: string | null;
  source: string;
  external_paper_id: string | null;
  // Phase 2: parsing state
  parse_status: "not_applicable" | "pending" | "parsing" | "parsed" | "failed";
  parsed_at: string | null;
  chunk_count: number;
  parsed_text_artifact_id: string | null;
  chunk_index_artifact_id: string | null;
  is_deleted: boolean;
  created_at: string;
  updated_at: string;
}

export interface PaperCreate {
  title: string;
  authors?: string[];
  year?: number;
  abstract?: string;
  doi?: string;
  arxiv_id?: string;
}

export type PaperUpdate = Partial<PaperCreate>;

export interface PaperListResponse {
  items: Paper[];
  total: number;
  limit: number;
  offset: number;
}

export interface Artifact {
  id: string;
  workspace_id: string;
  kind: string;
  file_path: string;
  original_filename: string | null;
  mime_type: string | null;
  size_bytes: number;
  is_deleted: boolean;
  created_at: string;
  updated_at: string;
}

export interface Task {
  id: string;
  workspace_id: string | null;
  task_type: string;
  status:
    | "queued"
    | "running"
    | "waiting_for_user"
    | "succeeded"
    | "failed"
    | "cancel_requested"
    | "cancelled";
  progress: number;
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  celery_task_id: string | null;
  is_deleted: boolean;
  created_at: string;
  updated_at: string;
}

export interface TaskListResponse {
  items: Task[];
  total: number;
  limit: number;
  offset: number;
}

export interface TimelineEvent {
  id: string;
  workspace_id: string;
  event_type: string;
  actor: string;
  subject_type: string | null;
  subject_id: string | null;
  payload: Record<string, unknown>;
  summary: string | null;
  created_at: string;
}

export interface TimelineListResponse {
  items: TimelineEvent[];
  total: number;
  limit: number;
  offset: number;
}

export interface KnowledgeItem {
  id: string;
  workspace_id: string;
  type: string;
  canonical_name: string;
  content: Record<string, unknown>;
  source_provenance: Record<string, unknown>;
  created_by: "user" | "agent" | "system";
  confidence: number;
  status: string;
  version: number;
  is_deleted: boolean;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeItemListResponse {
  items: KnowledgeItem[];
  total: number;
  limit: number;
  offset: number;
}
