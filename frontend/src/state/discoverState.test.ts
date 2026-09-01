import { describe, expect, it } from "vitest";
import { currentRunStage, currentRunStatus, DISCOVER_STAGE_LABELS, DISCOVER_STAGES, pollingInterval, selectedOpportunityCount, stageIndex, stageSummaryMessage, stageSummaryStatus } from "./discoverState";

describe("Discover state helpers", () => {
  it("keeps the complete stage order and unknown stages visible", () => {
    expect(DISCOVER_STAGES).toContain("fulltext_verification");
    expect(DISCOVER_STAGES).toContain("saved");
    expect(stageIndex("fulltext_verification")).toBe(6);
    expect(stageIndex("unexpected_stage")).toBe(-1);
  });

  it("describes partial external-search results without discarding them", () => {
    expect(DISCOVER_STAGE_LABELS.external_search).toBe("外部检索");
    const summaries = {
      external_search: {
        status: "succeeded_partial",
        successful_query_count: 3,
        failed_query_count: 2,
        query_failures: [{ status_code: 429 }, { status_code: 429 }],
      },
    };
    expect(stageSummaryStatus(summaries, "external_search")).toBe("succeeded_partial");
    expect(stageSummaryMessage(summaries, "external_search")).toContain("已完成 3/5 个检索方向");
    expect(stageSummaryMessage(summaries, "external_search")).toContain("频率限制 2 条");
  });

  it("keeps a non-critical partial search visually successful but explains the limitation", () => {
    const summaries = {
      external_search: {
        status: "succeeded",
        notice_level: "informational",
        successful_query_count: 11,
        failed_query_count: 1,
        candidate_count: 10,
        message: "外部文献初筛部分完成：11/12 个检索方向成功，已保留 10 篇候选。未完成原因：请求超时 1 条。",
      },
    };
    expect(stageSummaryMessage(summaries, "external_search")).toContain("11/12");
    expect(stageSummaryMessage(summaries, "external_search")).toContain("已保留 10 篇候选");
  });

  it("normalizes legacy external messages instead of showing raw limited wording", () => {
    const summaries = {
      external_search: {
        status: "succeeded_partial",
        successful_query_count: 4,
        failed_query_count: 8,
        candidate_count: 32,
        query_failures: [{ status_code: 504 }, { status_code: 502 }],
        message: "外部检索成功 4/12 条查询，8 条受限；已保留成功结果。",
      },
    };
    const detail = stageSummaryMessage(summaries, "external_search") || "";
    expect(detail).toContain("4/12");
    expect(detail).not.toContain("外部文献初筛部分完成");
    expect(detail).toContain("请求超时 1 条");
    expect(detail).toContain("网络/TLS异常 1 条");
    expect(detail).not.toContain("8 条受限");
  });

  it("uses low-frequency polling for waiting states and stops at terminal states", () => {
    expect(pollingInterval("running")).toBe(2000);
    expect(pollingInterval("waiting_for_fulltext")).toBe(5000);
    expect(pollingInterval("succeeded")).toBeNull();
    expect(pollingInterval("cancelled")).toBeNull();
  });

  it("does not read status before either run object has loaded", () => {
    expect(currentRunStatus(null, null)).toBeNull();
    expect(currentRunStatus(null, { id: "run-1", status: "queued" })).toBe("queued");
    expect(currentRunStatus({ id: "run-1", status: "running" }, { id: "run-1", status: "queued" })).toBe("running");
  });

  it("does not read stage before either run object has loaded", () => {
    expect(currentRunStage(null, null)).toBeNull();
    expect(currentRunStage(null, { id: "run-1", stage: "preflight" })).toBe("preflight");
    expect(currentRunStage({ id: "run-1", stage: "synthesis" }, { id: "run-1", stage: "preflight" })).toBe("synthesis");
  });

  it("counts opportunities only for the selected run", () => {
    const items = [{ discover_run_id: "run-a" }, { discover_run_id: "run-a" }, { discover_run_id: "run-b" }];
    expect(selectedOpportunityCount(items, "run-a")).toBe(2);
    expect(selectedOpportunityCount(items, null)).toBe(3);
  });
});
