import apiClient from "./client";

export interface AuthUser {
  id: string;
  email: string | null;
  display_name: string | null;
  status: string;
  roles: string[];
  is_platform_admin: boolean;
}

export interface LoginResponse {
  user: AuthUser;
}

export interface InviteValidation {
  valid: boolean;
  email: string | null;
  expires_at: string | null;
  message: string | null;
}

export interface InviteCreated {
  id: string;
  email: string;
  expires_at: string;
  token: string;
}

export interface InviteListItem {
  id: string;
  email: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

export interface UserListItem {
  id: string;
  email: string;
  display_name: string | null;
  status: string;
  account_type: string;
  roles: string[];
  last_login_at: string | null;
}

export interface AuditEventItem {
  id: string;
  user_id: string | null;
  event_type: string;
  target_id: string | null;
  created_at: string;
}

export interface AuthMessage {
  message: string;
}

export interface ForgotPasswordResponse extends AuthMessage {
  debug_token?: string | null;
}

export const authApi = {
  async me() {
    const { data } = await apiClient.get<AuthUser>("/auth/me");
    return data;
  },
  async login(email: string, password: string) {
    const { data } = await apiClient.post<LoginResponse>("/auth/login", { email, password });
    return data;
  },
  async logout() {
    const { data } = await apiClient.post<AuthMessage>("/auth/logout");
    return data;
  },
  async logoutAll() {
    const { data } = await apiClient.post<AuthMessage>("/auth/logout-all");
    return data;
  },
  async validateInvite(token: string) {
    const { data } = await apiClient.get<InviteValidation>("/auth/invites/validate", { params: { token } });
    return data;
  },
  async acceptInvite(token: string, password: string, displayName?: string) {
    const { data } = await apiClient.post<LoginResponse>("/auth/invites/accept", {
      token,
      password,
      display_name: displayName || null,
    });
    return data;
  },
  async forgotPassword(email: string) {
    const { data } = await apiClient.post<ForgotPasswordResponse>("/auth/forgot-password", { email });
    return data;
  },
  async resetPassword(token: string, password: string) {
    const { data } = await apiClient.post<AuthMessage>("/auth/reset-password", { token, password });
    return data;
  },
  async createInvite(payload: { email: string }) {
    const { data } = await apiClient.post<InviteCreated>("/admin/invites", payload);
    return data;
  },
  async listInvites() {
    const { data } = await apiClient.get<InviteListItem[]>("/admin/invites");
    return data;
  },
  async listUsers() {
    const { data } = await apiClient.get<UserListItem[]>("/admin/users");
    return data;
  },
  async disableUser(userId: string) {
    const { data } = await apiClient.post<AuthMessage>(`/admin/users/${userId}/disable`);
    return data;
  },
  async enableUser(userId: string) {
    const { data } = await apiClient.post<AuthMessage>(`/admin/users/${userId}/enable`);
    return data;
  },
  async listAudit() {
    const { data } = await apiClient.get<AuditEventItem[]>("/admin/audit");
    return data;
  },
};

export default authApi;
