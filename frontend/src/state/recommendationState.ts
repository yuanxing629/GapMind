type RecommendationRequestError = {
  response?: {
    status?: number;
    data?: {
      detail?: {
        error?: unknown;
        message?: unknown;
      };
    };
  };
};

/** 将 Semantic Scholar 失败转换为稳定且可操作的 UI 文案。 */
export function recommendationErrorMessage(error: unknown): string {
  const response = (error as RecommendationRequestError).response;
  const detail = response?.data?.detail;
  if (detail?.error === "semantic_scholar_error") {
    if (response?.status === 429) {
      return "外部文献服务请求频率受限，请稍后再刷新。";
    }
    if (response?.status === 504) {
      return "外部文献服务响应超时，请稍后重试。";
    }
    return "外部文献服务暂时不可用，请稍后重试。";
  }
  if (typeof detail?.message === "string" && detail.message.trim()) {
    return detail.message;
  }
  if (error instanceof Error && error.message) return error.message;
  return "请求失败";
}
