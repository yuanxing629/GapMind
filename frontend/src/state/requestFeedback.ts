import axios from "axios";

/**
* 让传输失败仍可操作，同时不在研究 workspace UI 中暴露原始浏览器、代理或
* 上游 service 错误。
 */
export function requestErrorMessage(error: unknown, fallback = "操作失败，请稍后重试。"): string {
  if (!axios.isAxiosError(error)) return fallback;

  const detail = error.response?.data?.detail;
  if (typeof detail === "object" && detail && "message" in detail && typeof detail.message === "string") {
    return detail.message;
  }

  if (!error.response) {
    if (error.code === "ECONNABORTED" || error.code === "ETIMEDOUT") {
      return "请求超时，请确认本地服务仍在运行后重试。";
    }
    return "无法连接到本地服务，请确认后端（8000）及必要依赖已启动。";
  }

  if (error.response.status === 401 || error.response.status === 403) return "当前操作没有权限，请确认访问身份后重试。";
  if (error.response.status === 404) return "请求的内容不存在，可能已被删除或发生变更。";
  if (error.response.status === 409) return "当前内容正在被处理，请稍候再试。";
  if (error.response.status === 429) return "请求过于频繁，请稍候再试。";
  if (error.response.status >= 500) return "本地服务暂时不可用，请稍后重试。";

  return fallback;
}
