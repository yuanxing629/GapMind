import { describe, expect, it } from "vitest";
import type { Paper, Task } from "../api/types/domain";
import { paperPipelineStatus } from "./paperProcessing";

const paper = {
  primary_artifact_id: "pdf-1",
  parse_status: "parsed",
  chunk_index_artifact_id: "chunks-1",
  parsed_markdown_artifact_id: "markdown-1",
  extract_status: "not_applicable",
} satisfies Pick<Paper, "primary_artifact_id" | "parse_status" | "chunk_index_artifact_id" | "parsed_markdown_artifact_id" | "extract_status">;

function task(taskType: string, status: Task["status"], paperId = "paper-1"): Task {
  return {
    id: `${taskType}-${status}`,
    workspace_id: "workspace-1",
    task_type: taskType,
    status,
    progress: status === "succeeded" ? 1 : 0,
    payload: { paper_id: paperId },
    result: null,
    error: status === "failed" ? "worker failed" : null,
    celery_task_id: null,
    is_deleted: false,
    created_at: "2026-09-07T00:00:00Z",
    updated_at: "2026-09-07T00:00:00Z",
  };
}

describe("paper processing status", () => {
  it("requires a successful indexing task before showing indexed", () => {
    expect(paperPipelineStatus(paper, "paper-1", [], "index")).toBe("pending");
    expect(paperPipelineStatus(paper, "paper-1", [task("embed_chunks", "failed")], "index")).toBe("failed");
    expect(paperPipelineStatus(paper, "paper-1", [task("embed_chunks", "succeeded")], "index")).toBe("succeeded");
  });

  it("uses an active task state and keeps papers without PDFs unavailable", () => {
    expect(paperPipelineStatus(paper, "paper-1", [task("extract_knowledge", "running")], "knowledge")).toBe("running");
    expect(paperPipelineStatus({ ...paper, primary_artifact_id: null }, "paper-1", [], "parse")).toBe("not_applicable");
    expect(paperPipelineStatus({ ...paper, parse_status: "failed" }, "paper-1", [task("extract_knowledge", "failed")], "knowledge")).toBe("not_applicable");
  });
});
