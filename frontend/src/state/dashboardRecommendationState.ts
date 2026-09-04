import type { PaperRecommendation, PaperRecommendationResponse } from "../api/recommendations";
import type { Workspace } from "../api/types/workspace";

export interface DashboardRecommendationEntry {
  workspace: Workspace;
  item: PaperRecommendation;
}

export const DASHBOARD_RECOMMENDATION_PER_WORKSPACE = 2;
export const DASHBOARD_RECOMMENDATION_MAX = 6;

export function dashboardRecommendationEntries(
  workspace: Workspace,
  response: PaperRecommendationResponse,
): DashboardRecommendationEntry[] {
  if (!response.has_profile) return [];
  return response.items
    .slice(0, DASHBOARD_RECOMMENDATION_PER_WORKSPACE)
    .map((item) => ({ workspace, item }));
}

/** 保持 workspace 顺序，同时允许缓存来源优先渲染。 */
export function aggregateDashboardRecommendations(
  sources: Workspace[],
  entriesByWorkspace: ReadonlyMap<string, DashboardRecommendationEntry[]>,
): DashboardRecommendationEntry[] {
  return sources
    .flatMap((workspace) => entriesByWorkspace.get(workspace.id) ?? [])
    .slice(0, DASHBOARD_RECOMMENDATION_MAX);
}
