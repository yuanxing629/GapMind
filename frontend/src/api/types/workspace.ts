export interface Workspace {
  id: string;
  name: string;
  description: string | null;
  topic: string | null;
  keywords: string[];
  goals: string | null;
  constraints: string | null;
  active_questions: string[];
  is_archived: boolean;
  is_deleted: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceCreate {
  name: string;
  description?: string;
  topic?: string;
  keywords?: string[];
  goals?: string;
  constraints?: string;
  active_questions?: string[];
}

export type WorkspaceUpdate = Partial<WorkspaceCreate>;

export interface WorkspaceListResponse {
  items: Workspace[];
  total: number;
  limit: number;
  offset: number;
}

export interface WorkspaceListParams {
  include_archived?: boolean;
  limit?: number;
  offset?: number;
}
