import apiClient from "./client";

export interface HealthResponse {
  status: string;
  env: string;
}

export async function healthCheck(): Promise<HealthResponse> {
  const resp = await apiClient.get<HealthResponse>("/health");
  return resp.data;
}
