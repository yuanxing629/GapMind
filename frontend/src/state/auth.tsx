import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import authApi, { type AuthUser } from "../api/auth";

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

  const refresh = async () => {
    try {
      const current = await authApi.me();
      setUser(current);
      return current;
    } catch {
      setUser(null);
      return null;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const handleExpired = () => setUser(null);
    window.addEventListener("gm-auth-expired", handleExpired);
    void refresh();
    return () => window.removeEventListener("gm-auth-expired", handleExpired);
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    loading,
    isAuthenticated: user !== null,
    login: async (email, password) => {
      const response = await authApi.login(email, password);
      setUser(response.user);
      setLoading(false);
      return response.user;
    },
    acceptInvite: async (token, password, displayName) => {
      const response = await authApi.acceptInvite(token, password, displayName);
      setUser(response.user);
      setLoading(false);
      return response.user;
    },
    logout: async () => {
      try {
        await authApi.logout();
      } finally {
        setUser(null);
      }
    },
    refresh,
  }), [loading, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
