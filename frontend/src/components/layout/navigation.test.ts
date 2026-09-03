import { describe, expect, it } from "vitest";
import { readingLibraryPath, readingPaperPath, resolveReadingWorkspace, selectedGlobalKey, selectedWorkspaceKey, workspaceNavigationPath } from "./navigation";

describe("navigation helpers", () => {
  it("keeps global navigation selected for nested routes", () => {
    expect(selectedGlobalKey("/workspaces/ws-1/discover/runs/run-1")).toBe("/discover");
    expect(selectedGlobalKey("/search?query=gnn")).toBe("/search");
    expect(selectedGlobalKey("/chat/conversation-1")).toBe("/chat");
    expect(selectedGlobalKey("/reading")).toBe("/reading");
    expect(selectedGlobalKey("/reading/paper-1")).toBe("/reading");
    expect(selectedGlobalKey("/")).toBe("/");
  });

  it("selects lifecycle entries for workspace and assistant routes", () => {
    expect(selectedGlobalKey("/workspaces/ws-1/knowledge")).toBe("/knowledge");
    expect(selectedGlobalKey("/workspaces/ws-1/plans")).toBe("/plan");
    expect(selectedGlobalKey("/workspaces/ws-1/assistant", "?mode=code_generation")).toBe("/execute");
    expect(selectedGlobalKey("/chat/new", "?mode=respond")).toBe("/respond");
  });

  it("keeps workspace navigation selected for graph and discover details", () => {
    expect(selectedWorkspaceKey("/workspaces/ws-1/assistant/conversation-1")).toBe("assistant");
    expect(selectedWorkspaceKey("/workspaces/ws-1/knowledge/graph")).toBe("knowledge");
    expect(selectedWorkspaceKey("/workspaces/ws-1/discover/opportunities/op-1")).toBe("discover");
    expect(selectedWorkspaceKey("/workspaces/ws-1/settings")).toBe("settings");
  });

  it("provides compatibility-safe workspace destinations", () => {
    expect(workspaceNavigationPath("ws-1", "overview")).toBe("/workspaces/ws-1/overview");
    expect(workspaceNavigationPath("ws-1", "papers")).toBe("/workspaces/ws-1/papers");
    expect(workspaceNavigationPath("ws-1", "assistant")).toBe("/workspaces/ws-1/assistant");
    expect(workspaceNavigationPath("ws-1", "plans")).toBe("/workspaces/ws-1/plans");
    expect(selectedWorkspaceKey("/workspaces/ws-1/plans")).toBe("plans");
  });

  it("builds reading destinations with workspace context", () => {
    expect(readingLibraryPath("ws/1")).toBe("/reading?workspace_id=ws%2F1");
    expect(readingLibraryPath()).toBe("/reading");
    expect(readingPaperPath("paper/1")).toBe("/reading/paper%2F1");
  });

  it("resolves reading workspace without silently accepting an invalid URL workspace", () => {
    expect(resolveReadingWorkspace("ws-2", "ws-1", ["ws-1", "ws-2"])).toEqual({ workspaceId: "ws-2", invalidRequested: false });
    expect(resolveReadingWorkspace("missing", "ws-1", ["ws-1"])).toEqual({ invalidRequested: true });
    expect(resolveReadingWorkspace(null, "ws-1", ["ws-1", "ws-2"])).toEqual({ workspaceId: "ws-1", invalidRequested: false });
    expect(resolveReadingWorkspace(null, null, ["ws-1"])).toEqual({ workspaceId: "ws-1", invalidRequested: false });
  });
});
