import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import authApi, { type AuthUser } from "../api/auth";
import { useAppStore } from "../store/appStore";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<AuthUser>;
  acceptInvite: (token: string, password: string, displayName?: string) => Promise<AuthUser>;
  logout: () => Promise<void>;
  refresh: () => Promise<AuthUser | null>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const setCurrentWorkspace = useAppStore((state) => state.setCurrentWorkspace);

  const refresh = useCallback(async () => {
    try {
      const current = await authApi.me();
      setUser(current);
      return current;
    } catch {
      setUser(null);
      setCurrentWorkspace(null, null);
      return null;
    } finally {
      setLoading(false);
    }
  }, [setCurrentWorkspace]);

  useEffect(() => {
    const handleExpired = () => {
      setUser(null);
      setCurrentWorkspace(null, null);
    };
    window.addEventListener("gm-auth-expired", handleExpired);
    void refresh();
    return () => window.removeEventListener("gm-auth-expired", handleExpired);
  }, [refresh, setCurrentWorkspace]);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    loading,
    isAuthenticated: user !== null,
    login: async (email, password) => {
      const response = await authApi.login(email, password);
      setCurrentWorkspace(null, null);
      setUser(response.user);
      setLoading(false);
      return response.user;
    },
    acceptInvite: async (token, password, displayName) => {
      const response = await authApi.acceptInvite(token, password, displayName);
      setCurrentWorkspace(null, null);
      setUser(response.user);
      setLoading(false);
      return response.user;
    },
    logout: async () => {
      try {
        await authApi.logout();
      } finally {
        setUser(null);
        setCurrentWorkspace(null, null);
      }
    },
    refresh,
  }), [loading, refresh, setCurrentWorkspace, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
