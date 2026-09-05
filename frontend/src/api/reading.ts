import apiClient, { apiBaseURL } from "./client";

export type ReadingStatus = "unread" | "reading" | "completed";
export type AnnotationKind = "note" | "highlight" | "underline";

export interface AnnotationRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface ReadingPaper {
  reading_item_id: string;
  paper_id: string;
  workspace_id: string;
  workspace_name: string | null;
  title: string;
  authors: string[];
  year: number | null;
  abstract: string | null;
  doi: string | null;
  arxiv_id: string | null;
  source: string;
  external_paper_id: string | null;
  primary_artifact_id: string | null;
  parse_status: string;
  parsed_markdown_artifact_id: string | null;
  chunk_count: number;
  reading_status: ReadingStatus;
  last_read_page: number;
  last_read_at: string | null;
  added_at: string;
  updated_at: string;
}

export interface ReadingPaperListResponse {
  items: ReadingPaper[];
  total: number;
  limit: number;
  offset: number;
}

export interface PaperAnnotation {
  id: string;
  paper_id: string;
  workspace_id: string;
  artifact_id: string | null;
  kind: AnnotationKind;
  page_number: number;
  selected_text: string | null;
  note_content: string;
  color: string;
  rects: AnnotationRect[];
  source_text_hash: string | null;
  created_at: string;
  updated_at: string;
}

export interface PaperAnnotationInput {
  kind?: AnnotationKind;
  page_number: number;
  selected_text?: string;
  note_content: string;
  color?: string;
  rects?: AnnotationRect[];
  source_text_hash?: string;
}

const apiBase = apiBaseURL.replace(/\/$/, "");

function paperArtifactUrl(workspaceId: string, artifactId: string): string {
  return `${apiBase}/workspaces/${encodeURIComponent(workspaceId)}/artifacts/${encodeURIComponent(artifactId)}/view`;
}

export function paperArtifactViewUrl(
  workspaceId: string,
  artifactId: string,
  page = 1,
): string {
  return `${paperArtifactUrl(workspaceId, artifactId)}#page=${Math.max(1, page)}`;
}

export const readingApi = {
  async list(params: {
    workspace_id?: string;
    reading_status?: ReadingStatus;
    limit?: number;
    offset?: number;
  } = {}): Promise<ReadingPaperListResponse> {
    const { data } = await apiClient.get<ReadingPaperListResponse>("/reading/papers", {
      params,
    });
    return data;
  },

  async get(paperId: string): Promise<ReadingPaper> {
    const { data } = await apiClient.get<ReadingPaper>(`/reading/papers/${encodeURIComponent(paperId)}`);
    return data;
  },

  async ensure(paperId: string): Promise<ReadingPaper> {
    try {
      return await this.get(paperId);
    } catch (error) {
      const status = (error as { response?: { status?: number } }).response?.status;
      if (status !== 404) throw error;
      return this.add(paperId);
    }
  },

  async add(paperId: string): Promise<ReadingPaper> {
    const { data } = await apiClient.post<ReadingPaper>(`/reading/papers/${encodeURIComponent(paperId)}`);
    return data;
  },

  async remove(paperId: string): Promise<void> {
    await apiClient.delete(`/reading/papers/${encodeURIComponent(paperId)}`);
  },

  async updateProgress(
    paperId: string,
    payload: { page_number: number; status?: ReadingStatus },
  ): Promise<ReadingPaper> {
    const { data } = await apiClient.patch<ReadingPaper>(
      `/reading/papers/${encodeURIComponent(paperId)}/progress`,
      payload,
    );
    return data;
  },

  async listAnnotations(paperId: string): Promise<PaperAnnotation[]> {
    const { data } = await apiClient.get<PaperAnnotation[]>(
      `/reading/papers/${encodeURIComponent(paperId)}/annotations`,
    );
    return data;
  },

  async createAnnotation(paperId: string, payload: PaperAnnotationInput): Promise<PaperAnnotation> {
    const { data } = await apiClient.post<PaperAnnotation>(
      `/reading/papers/${encodeURIComponent(paperId)}/annotations`,
      payload,
    );
    return data;
  },

  async updateAnnotation(annotationId: string, payload: Partial<PaperAnnotationInput>): Promise<PaperAnnotation> {
    const { data } = await apiClient.patch<PaperAnnotation>(
      `/reading/annotations/${encodeURIComponent(annotationId)}`,
      payload,
    );
    return data;
  },

  async removeAnnotation(annotationId: string): Promise<void> {
    await apiClient.delete(`/reading/annotations/${encodeURIComponent(annotationId)}`);
  },
};

export default readingApi;
