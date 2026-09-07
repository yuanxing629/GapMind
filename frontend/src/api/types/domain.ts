// Paper、Task、Timeline、Artifact 的手写类型别名——从自动生成的 OpenAPI schemas 重新导出。
//
// 修改对应 Pydantic models 后运行 `npm run gen:api` 保持同步。不要在这里添加手写字段；
// 如果缺少字段，应添加到后端 schema 并重新生成。
//
// Omit+Pick 覆盖将 Pydantic `dict[str, Any]`（openapi-typescript 生成的
// `Record<string, never>`）放宽为更易用的 `Record<string, unknown>`，使组件代码
// 无需类型转换即可遍历。

import type { components } from "./api.gen";

type LooseDictField<T, K extends keyof T> = Omit<T, K> & {
  [P in K]: Record<string, unknown> | Extract<T[P], null | undefined>;
};

export type Paper = components["schemas"]["PaperRead"];
export type PaperCreate = components["schemas"]["PaperCreate"];
export type PaperUpdate = components["schemas"]["PaperUpdate"];
export type PaperListResponse = components["schemas"]["PaperListResponse"];

export type Artifact = components["schemas"]["ArtifactRead"];

type _TaskRaw = components["schemas"]["TaskRead"];
export type Task = LooseDictField<_TaskRaw, "payload" | "result">;
type _TaskListRaw = components["schemas"]["TaskListResponse"];
export type TaskListResponse = Omit<_TaskListRaw, "items"> & { items: Task[] };

type _TimelineEventRaw = components["schemas"]["TimelineEventRead"];
export type TimelineEvent = LooseDictField<_TimelineEventRaw, "payload">;
export type TimelineListResponse = components["schemas"]["TimelineListResponse"];

type _ExtractionRejectionRaw = components["schemas"]["ExtractionRejectionRead"];
export type ExtractionRejection = LooseDictField<_ExtractionRejectionRaw, "raw_payload">;
export type ExtractionRejectionListResponse = components["schemas"]["ExtractionRejectionListResponse"];
