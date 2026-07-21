import apiClient from "./client";
import type { TimelineListResponse } from "./types/domain";

export const timelineApi = {
  async list(
    workspaceId: string,
    params: {
      subject_type?: string;
      subject_id?: string;
      event_type?: string;
      limit?: number;
      offset?: number;
    } = {}
  ): Promise<TimelineListResponse> {
    const resp = await apiClient.get<TimelineListResponse>(
      `/workspaces/${workspaceId}/timeline`,
      {
        params: {
          subject_type: params.subject_type,
          subject_id: params.subject_id,
          event_type: params.event_type,
          limit: params.limit ?? 100,
          offset: params.offset ?? 0,
        },
      }
    );
    return resp.data;
  },
};

export default timelineApi;
