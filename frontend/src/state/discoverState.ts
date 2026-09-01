export const DISCOVER_STAGES = [
  "preflight",
  "workspace_retrieval",
  "similar_work",
  "counter_evidence",
  "external_search",
  "external_selection",
  "fulltext_verification",
  "synthesis",
  "saved",
] as const;

export type DiscoverStage = (typeof DISCOVER_STAGES)[number];

export const DISCOVER_STAGE_LABELS: Record<DiscoverStage, string> = {
  preflight: "输入检查",
  workspace_retrieval: "工作区检索",
  similar_work: "相似工作",
  counter_evidence: "反证检索",
  external_search: "外部检索",
  external_selection: "外部论文选择",
  fulltext_verification: "全文核验",
  synthesis: "候选综合",
  saved: "保存结果",
};

export function stageSummaryStatus(
  stageSummaries: Record<string, unknown> | null | undefined,
  stage: DiscoverStage,
): string | null {
  const raw = stageSummaries?.[stage];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const status = (raw as { status?: unknown }).status;
  return typeof status === "string" ? status : null;
}

function externalFailureKind(record: { failure_kind?: unknown; status_code?: unknown }): string {
  if (typeof record.failure_kind === "string") return record.failure_kind;
  const statusCode = typeof record.status_code === "number" ? record.status_code : Number(record.status_code);
  if (statusCode === 429) return "rate_limited";
  if (statusCode === 504) return "timeout";
  if (statusCode === 502) return "network_error";
  if (statusCode >= 500) return "upstream_error";
  return "request_error";
}

export function stageSummaryMessage(
  stageSummaries: Record<string, unknown> | null | undefined,
  stage: DiscoverStage,
): string | null {
  const raw = stageSummaries?.[stage];
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const summary = raw as {
    status?: unknown;
    error?: unknown;
    successful_query_count?: unknown;
    failed_query_count?: unknown;
    query_success_rate?: unknown;
    notice_level?: unknown;
    message?: unknown;
    query_failures?: unknown;
    failure_counts?: unknown;
    exact_lookup_failure_count?: unknown;
    candidate_count?: unknown;
  };
  const status = typeof summary.status === "string" ? summary.status : null;
  const failures = Array.isArray(summary.query_failures) ? summary.query_failures : [];
  const failureCounts: Record<string, number> = {};
  if (summary.failure_counts && typeof summary.failure_counts === "object" && !Array.isArray(summary.failure_counts)) {
    Object.entries(summary.failure_counts as Record<string, unknown>).forEach(([kind, count]) => {
      if (typeof count === "number" && count > 0) failureCounts[kind] = count;
    });
  }
  if (Object.keys(failureCounts).length === 0) {
    failures.forEach((item) => {
      if (!item || typeof item !== "object") return;
      const kind = externalFailureKind(item as { failure_kind?: unknown; status_code?: unknown });
      failureCounts[kind] = (failureCounts[kind] || 0) + 1;
    });
  }
  const failureLabels: Record<string, string> = {
    rate_limited: "频率限制",
    timeout: "请求超时",
    network_error: "网络/TLS异常",
    upstream_error: "外部服务异常",
    request_error: "请求异常",
  };
  const failureReason = Object.entries(failureCounts)
    .filter(([, count]) => count > 0)
    .map(([kind, count]) => `${failureLabels[kind] || "请求异常"} ${count} 条`)
    .join("、");
  const candidateCount = Number(summary.candidate_count ?? 0);
  const retained = candidateCount > 0 ? `已保留 ${candidateCount} 篇候选。` : "已保留成功结果。";
  const exactLookupFailureCount = Number(summary.exact_lookup_failure_count ?? 0);
  const exactLookupNotice = exactLookupFailureCount > 0
    ? `另有 ${exactLookupFailureCount} 条方法精确查找未完成。`
    : "";
  if (status === "failed") {
    if (stage === "external_search") {
      const failedCount = Number(summary.failed_query_count ?? failures.length);
      return `外部文献初筛未完成：${failedCount || "所有"} 个检索方向未获得结果${failureReason ? `。原因：${failureReason}` : "。"}`;
    }
    return typeof summary.error === "string" ? summary.error : "该阶段执行失败";
  }
  if (status === "succeeded" && summary.notice_level === "informational" && (Number(summary.failed_query_count ?? 0) > 0 || Number(summary.exact_lookup_failure_count ?? 0) > 0)) {
    return `已完成 ${Number(summary.successful_query_count ?? 0)}/${Number(summary.successful_query_count ?? 0) + Number(summary.failed_query_count ?? 0)} 个检索方向。${retained}${failureReason ? `未完成原因：${failureReason}。` : ""}${exactLookupNotice}`;
  }
  if (status === "succeeded_partial") {
    return `已完成 ${Number(summary.successful_query_count ?? 0)}/${Number(summary.successful_query_count ?? 0) + Number(summary.failed_query_count ?? 0)} 个检索方向。${retained}${failureReason ? `未完成原因：${failureReason}。` : ""}${exactLookupNotice}`;
  }
  if (status === "succeeded_empty") return "检索已执行，但没有返回候选论文";
  if (status === "skipped") return "该阶段已由用户跳过";
  return null;
}

export const TERMINAL_RUN_STATUSES = new Set(["succeeded", "failed", "cancelled"]);

export function stageIndex(stage: string | null | undefined): number {
  const index = DISCOVER_STAGES.indexOf(stage as (typeof DISCOVER_STAGES)[number]);
  return index;
}

export function pollingInterval(status: string | null | undefined): number | null {
  if (!status || TERMINAL_RUN_STATUSES.has(status)) return null;
  if (status === "waiting_for_user" || status === "waiting_for_fulltext") return 5000;
  return 2000;
}

export function currentRunStatus(
  runDetail: { id: string; status: string } | null,
  selectedRun: { id: string; status: string } | null,
): string | null {
  if (runDetail && selectedRun && runDetail.id === selectedRun.id) return runDetail.status;
  return selectedRun?.status ?? null;
}

export function currentRunStage(
  runDetail: { id: string; stage: string } | null,
  selectedRun: { id: string; stage: string } | null,
): string | null {
  if (runDetail && selectedRun && runDetail.id === selectedRun.id) return runDetail.stage;
  return selectedRun?.stage ?? null;
}

export function selectedOpportunityCount(
  opportunities: Array<{ discover_run_id?: string | null }>,
  runId: string | null,
): number {
  return runId ? opportunities.filter((item) => item.discover_run_id === runId).length : opportunities.length;
}
