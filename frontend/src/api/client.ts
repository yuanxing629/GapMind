import axios from "axios";

export const apiBaseURL =
  import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/v1";

export const apiClient = axios.create({
  baseURL: apiBaseURL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

export function getCsrfToken() {
  if (typeof document === "undefined") return undefined;
  const prefix = "gm_csrf=";
  const value = document.cookie.split("; ").find((item) => item.startsWith(prefix));
  return value ? decodeURIComponent(value.slice(prefix.length)) : undefined;
}

apiClient.interceptors.request.use((config) => {
  const csrf = getCsrfToken();
  if (csrf) config.headers.set("X-CSRF-Token", csrf);
  return config;
});

apiClient.interceptors.response.use(undefined, (error) => {
  const url = String(error?.config?.url || "");
  if (error?.response?.status === 401 && !url.includes("/auth/login") && !url.includes("/auth/invites")) {
    if (typeof window !== "undefined") window.dispatchEvent(new Event("gm-auth-expired"));
  }
  return Promise.reject(error);
});

export default apiClient;
