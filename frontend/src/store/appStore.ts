import { create } from "zustand";

const ACTIVE_WORKSPACE_STORAGE_KEY = "gapmind.active-workspace";

interface AppState {
  currentWorkspaceId: string | null;
  currentWorkspaceName: string | null;
  setCurrentWorkspace: (id: string | null, name?: string | null) => void;
}

function readStoredWorkspace(): { id: string; name: string | null } | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem(ACTIVE_WORKSPACE_STORAGE_KEY);
    if (!raw) return null;
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || !("id" in value) || typeof value.id !== "string") {
      return null;
    }
    return {
      id: value.id,
      name: "name" in value && typeof value.name === "string" ? value.name : null,
    };
  } catch {
    return null;
  }
}

function persistWorkspace(id: string | null, name: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (id) {
      window.sessionStorage.setItem(
        ACTIVE_WORKSPACE_STORAGE_KEY,
        JSON.stringify({ id, name }),
      );
    } else {
      window.sessionStorage.removeItem(ACTIVE_WORKSPACE_STORAGE_KEY);
    }
  } catch {
    // 浏览器禁用 sessionStorage 时仍可使用内存中的课题状态。
  }
}

const storedWorkspace = readStoredWorkspace();

export const useAppStore = create<AppState>((set) => ({
  currentWorkspaceId: storedWorkspace?.id ?? null,
  currentWorkspaceName: storedWorkspace?.name ?? null,
  setCurrentWorkspace: (id, name = null) => {
    persistWorkspace(id, name);
    set({ currentWorkspaceId: id, currentWorkspaceName: name });
  },
}));
