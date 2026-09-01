const ACTIVE_CANDIDATE_STATUSES = new Set(["selected", "imported_pending_parse", "verified"]);

export function externalSelectionIsOpen(
  runStatus: string | null | undefined,
  runStage: string | null | undefined,
): boolean {
  return runStatus === "waiting_for_user" && runStage === "external_selection";
}

export function canSelectExternalCandidate(
  runStatus: string | null | undefined,
  runStage: string | null | undefined,
  candidateStatus: string,
): boolean {
  return externalSelectionIsOpen(runStatus, runStage) && !ACTIVE_CANDIDATE_STATUSES.has(candidateStatus);
}

export function externalCandidateActionLabel(
  runStatus: string | null | undefined,
  runStage: string | null | undefined,
  candidateStatus: string,
): string {
  if (candidateStatus === "selected") return "正在启动…";
  if (candidateStatus === "imported_pending_parse") return "正在处理…";
  if (candidateStatus === "verified") return "已完成全文核验";
  if (externalSelectionIsOpen(runStatus, runStage)) {
    return ["no_pdf", "import_failed", "verification_failed"].includes(candidateStatus)
      ? "重新获取 PDF 并核验"
      : "导入并核验";
  }
  if (runStatus === "waiting_for_fulltext" || runStage === "fulltext_verification") {
    if (candidateStatus === "unverified") return "未选择（当前批次处理中）";
    if (["no_pdf", "import_failed", "verification_failed"].includes(candidateStatus)) return "本次核验未完成";
    return "等待当前全文核验";
  }
  if (["succeeded", "failed", "cancelled"].includes(runStatus ?? "")) {
    if (candidateStatus === "unverified") return "本轮未选择";
    if (["no_pdf", "import_failed", "verification_failed"].includes(candidateStatus)) return "本轮核验未完成";
    return "本轮已结束";
  }
  return "当前不可操作";
}
