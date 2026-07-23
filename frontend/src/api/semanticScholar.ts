import apiClient from "./client";
import type { Paper } from "./types/domain";

export type SemanticScholarSort =
  | "relevance"
  | "publicationDate:asc"
  | "publicationDate:desc"
  | "citationCount:asc"
  | "citationCount:desc";

export interface SemanticScholarAuthor {
  authorId: string | null;
  name: string | null;
}

export interface SemanticScholarPaper {
  paperId: string;
  corpusId: number | null;
  externalIds: Record<string, unknown> | null;
  url: string | null;
  title: string | null;
  abstract: string | null;
  year: number | null;
  publicationDate: string | null;
  authors: SemanticScholarAuthor[];
  venue: string | null;
  citationCount: number | null;
  referenceCount: number | null;
  influentialCitationCount: number | null;
  isOpenAccess: boolean | null;
  openAccessPdf: {
    url?: string | null;
    status?: string | null;
    license?: string | null;
    disclaimer?: string | null;
  } | null;
  fieldsOfStudy: string[] | null;
  s2FieldsOfStudy: Array<{ category?: string; source?: string }> | null;
  publicationTypes: string[] | null;
  tldr: { text?: string; model?: string } | null;
}

export interface SemanticScholarSearchParams {
  query: string;
  year_from?: number;
  year_to?: number;
  min_citation_count?: number;
  open_access?: boolean;
  fields_of_study?: string;
  publication_types?: string;
  venue?: string;
  sort?: SemanticScholarSort;
  limit?: number;
  offset?: number;
  token?: string;
}

export interface SemanticScholarSearchResponse {
  total: number;
  offset: number;
  next: number | null;
  token: string | null;
  data: SemanticScholarPaper[];
}

export const semanticScholarApi = {
  async search(
    params: SemanticScholarSearchParams
  ): Promise<SemanticScholarSearchResponse> {
    const resp = await apiClient.get<SemanticScholarSearchResponse>("/papers/search", {
      params,
    });
    return resp.data;
  },

  async importToWorkspace(
    workspaceId: string,
    semanticScholarPaperId: string
  ): Promise<Paper> {
    const resp = await apiClient.post<Paper>(
      `/workspaces/${workspaceId}/papers/import-from-s2`,
      { semantic_scholar_paper_id: semanticScholarPaperId }
    );
    return resp.data;
  },
};

export default semanticScholarApi;
