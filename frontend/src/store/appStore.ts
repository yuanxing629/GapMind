import { create } from "zustand";

interface AppState {
  currentWorkspaceId: string | null;
  setCurrentWorkspace: (id: string | null) => void;
}

export const useAppStore = create<AppState>((set) => ({
  currentWorkspaceId: null,
  setCurrentWorkspace: (id) => set({ currentWorkspaceId: id }),
}));
