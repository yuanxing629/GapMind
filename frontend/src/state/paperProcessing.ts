import type { Paper, Task } from "../api/types/domain";

export type PaperTaskType = "parse_pdf" | "embed_chunks" | "extract_knowledge";
export type PaperPipelineStatus = "not_applicable" | "pending" | "running" | "failed" | "succeeded";

function taskPaperId(task: Task): string | null {
  const payload = task.payload as Record<string, unknown> | undefined;
  return typeof payload?.paper_id === "string" ? payload.paper_id : null;
}

export function latestPaperTask(
  tasks: Task[],
  paperId: string,
  taskType: PaperTaskType,
): Task | undefined {
  return tasks
    .filter((task) => task.task_type === taskType && taskPaperId(task) === paperId)
    .sort((left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at))[0];
}

export function isActivePaperTask(task: Task | undefined): boolean {
  return task?.status === "queued" || task?.status === "running";
}

export function paperPipelineStatus(
  paper: Pick<Paper, "primary_artifact_id" | "parse_status" | "chunk_index_artifact_id" | "parsed_markdown_artifact_id" | "extract_status">,
  paperId: string,
  tasks: Task[],
  kind: "parse" | "index" | "knowledge",
): PaperPipelineStatus {
  const taskType: PaperTaskType = kind === "parse"
    ? "parse_pdf"
    : kind === "index"
      ? "embed_chunks"
      : "extract_knowledge";
  const task = latestPaperTask(tasks, paperId, taskType);

  if (kind === "parse") {
    if (!paper.primary_artifact_id) return "not_applicable";
    if (isActivePaperTask(task)) return task?.status === "running" ? "running" : "pending";
    if (task?.status === "failed") return "failed";
    if (paper.parse_status === "parsed") return "succeeded";
    if (paper.parse_status === "failed") return "failed";
    return "pending";
  }

  if (paper.parse_status !== "parsed") return "not_applicable";
  if (kind === "index") {
    if (!paper.chunk_index_artifact_id) return "not_applicable";
    if (isActivePaperTask(task)) return task?.status === "running" ? "running" : "pending";
    if (task?.status === "failed") return "failed";
    return task?.status === "succeeded" ? "succeeded" : "pending";
  }
  if (!paper.parsed_markdown_artifact_id) return "not_applicable";
  if (isActivePaperTask(task)) return task?.status === "running" ? "running" : "pending";
  if (task?.status === "failed") return "failed";
  if (paper.extract_status === "extracted") return "succeeded";
  if (paper.extract_status === "failed") return "failed";
  return "pending";
}

export function pipelineActionLabel(kind: "parse" | "index" | "knowledge", status: PaperPipelineStatus): string {
  const labels = {
    parse: status === "failed" ? "重试解析" : "解析论文",
    index: status === "failed" ? "重试全文索引" : "全文索引",
    knowledge: status === "failed" ? "重试知识提取" : "知识提取",
  };
  return labels[kind];
}
