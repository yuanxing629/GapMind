// Knowledge 类型别名——从自动生成的 OpenAPI schemas 重新导出。
// 修改对应 Pydantic models 后运行 `npm run gen:api`，保持两边同步。
//
// 不要在这里添加手写字段。下面的 Omit+Pick 覆盖是唯一例外：它们将 Pydantic
// `dict[str, Any]`（openapi-typescript 会生成 `Record<string, never>`）放宽为
// 更易用的 `Record<string, unknown>`，使组件代码无需类型转换即可遍历。

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

// 从生成 schemas 派生的枚举风格字符串字面量类型。
export type KnowledgeType = KnowledgeItem["type"];
export type KnowledgeStatus = KnowledgeItem["status"];
