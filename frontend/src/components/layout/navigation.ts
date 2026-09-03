export const WORKSPACE_NAVIGATION = [
  { key: "overview", label: "概览" },
  { key: "papers", label: "文献" },
  { key: "assistant", label: "AI 助手" },
  { key: "knowledge", label: "知识" },
  { key: "gap-board", label: "研究空白" },
  { key: "discover", label: "发现" },
  { key: "plans", label: "研究中心" },
  { key: "activity", label: "动态" },
  { key: "settings", label: "设置" },
] as const;

export function selectedGlobalKey(pathname: string, search = ""): string {
  if (pathname === "/") return "/";
  const mode = new URLSearchParams(search).get("mode");
  if (mode === "code_generation") return "/execute";
  if (mode === "analyze") return "/analyze";
  if (mode === "write") return "/publish";
  if (mode === "respond") return "/respond";
  if (pathname.includes("/knowledge")) return "/knowledge";
  if (pathname.includes("/discover")) return "/discover";
  if (pathname.includes("/plans")) return "/plan";
  if (pathname.startsWith("/search")) return "/search";
  if (pathname.startsWith("/chat")) return "/chat";
  if (pathname.startsWith("/reading")) return "/reading";
  if (pathname.startsWith("/workspaces")) return "/workspaces";
  return "/";
}

export function readingLibraryPath(workspaceId?: string | null): string {
  if (!workspaceId) return "/reading";
  return `/reading?workspace_id=${encodeURIComponent(workspaceId)}`;
}

export function readingPaperPath(paperId: string): string {
  return `/reading/${encodeURIComponent(paperId)}`;
}

export function resolveReadingWorkspace(
  requestedWorkspaceId: string | null | undefined,
  currentWorkspaceId: string | null | undefined,
  availableWorkspaceIds: readonly string[],
): { workspaceId?: string; invalidRequested: boolean } {
  if (requestedWorkspaceId) {
    return availableWorkspaceIds.includes(requestedWorkspaceId)
      ? { workspaceId: requestedWorkspaceId, invalidRequested: false }
      : { invalidRequested: true };
  }
  if (currentWorkspaceId && availableWorkspaceIds.includes(currentWorkspaceId)) {
    return { workspaceId: currentWorkspaceId, invalidRequested: false };
  }
  return { workspaceId: availableWorkspaceIds[0], invalidRequested: false };
}

export function workspaceNavigationPath(workspaceId: string, key: string): string {
  if (key === "overview") return `/workspaces/${workspaceId}/overview`;
  if (key === "knowledge") return `/workspaces/${workspaceId}/knowledge`;
  if (key === "gap-board") return `/workspaces/${workspaceId}/gap-board`;
  if (key === "discover") return `/workspaces/${workspaceId}/discover`;
  return `/workspaces/${workspaceId}/${key}`;
}

export function selectedWorkspaceKey(pathname: string): string {
  if (pathname.includes("/assistant")) return "assistant";
  if (pathname.includes("/knowledge")) return "knowledge";
  if (pathname.includes("/gap-board")) return "gap-board";
  if (pathname.includes("/discover")) return "discover";
  const matched = WORKSPACE_NAVIGATION.find((item) => pathname.includes(`/${item.key}`));
  return matched?.key ?? "overview";
}
