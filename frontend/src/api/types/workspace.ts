// Workspace 类型别名——全部从自动生成的 OpenAPI schemas 重新导出。
// 修改后端 Pydantic models 后运行 `npm run gen:api`，保持两边同步。
//
// 不要在这里添加手写字段。后端缺少字段时，应添加到 Pydantic schema 并重新生成。

import type { components } from "./api.gen";

export type Workspace = components["schemas"]["WorkspaceRead"];
export type WorkspaceCreate = components["schemas"]["WorkspaceCreate"];
export type WorkspaceUpdate = components["schemas"]["WorkspaceUpdate"];
export type WorkspaceListResponse = components["schemas"]["WorkspaceListResponse"];
export type WorkspaceReadiness = components["schemas"]["WorkspaceReadiness"];
export type ReadinessDimension = components["schemas"]["ReadinessDimension"];
export type ReadinessBlockingAction = components["schemas"]["ReadinessBlockingAction"];
export type ReadinessRecommendedAction = components["schemas"]["ReadinessRecommendedAction"];

export interface WorkspaceListParams {
  include_archived?: boolean;
  limit?: number;
  offset?: number;
}
