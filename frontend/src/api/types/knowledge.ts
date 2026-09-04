// Knowledge type aliases — re-exported from the auto-generated OpenAPI
// schemas. Run `npm run gen:api` after touching the corresponding Pydantic
// models to keep these in sync.
//
// Do NOT add hand-written fields here. The Omit+Pick overrides below are
// the only exception: they relax Pydantic `dict[str, Any]` (which
// openapi-typescript renders as `Record<string, never>`) to a friendlier
// `Record<string, unknown>` so component code can iterate without casts.

import type { components } from "./api.gen";

type LooseDictField<T, K extends keyof T> = Omit<T, K> & {
  [P in K]: NonNullable<T[P]> | undefined extends T[P]
    ? Record<string, unknown> | undefined
    : Record<string, unknown>;
};

type _KnowledgeItemRaw = components["schemas"]["KnowledgeItemRead"];
export type KnowledgeItem = LooseDictField<_KnowledgeItemRaw, "content" | "source_provenance">;

type _KnowledgeRelationRaw = components["schemas"]["KnowledgeRelationRead"];
export type KnowledgeRelation = LooseDictField<_KnowledgeRelationRaw, "payload">;

export type KnowledgeItemListResponse = components["schemas"]["KnowledgeItemListResponse"];
export type KnowledgeRelationListResponse = components["schemas"]["KnowledgeRelationListResponse"];

export type EvidenceSpan = components["schemas"]["EvidenceSpanRead"];
export type EvidenceSpanListResponse = components["schemas"]["EvidenceSpanListResponse"];

type _GraphNodeRaw = components["schemas"]["KnowledgeGraphNodeRead"];
export type KnowledgeGraphNode = LooseDictField<_GraphNodeRaw, "content">;

export type KnowledgeGraphEdge = components["schemas"]["KnowledgeGraphEdgeRead"];
export type KnowledgeGraphResponse = components["schemas"]["KnowledgeGraphResponse"];
export type KnowledgeGraphSearchResult = components["schemas"]["KnowledgeGraphSearchResult"];
export type KnowledgeGraphSearchResponse = components["schemas"]["KnowledgeGraphSearchResponse"];
export type GraphRAGPath = components["schemas"]["GraphRAGPathRead"];
export type GraphRAGEvidence = components["schemas"]["GraphRAGEvidenceRead"];
export type GraphRAGAudit = components["schemas"]["GraphRetrievalAuditRead"];

export type EvidenceContext = components["schemas"]["EvidenceContextRead"];

// Enum-style string literal types derived from the generated schemas.
export type KnowledgeType = KnowledgeItem["type"];
export type KnowledgeStatus = KnowledgeItem["status"];
