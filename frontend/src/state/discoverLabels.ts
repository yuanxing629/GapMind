import { DISCOVER_STAGE_LABELS, type DiscoverStage } from "./discoverState";

const RELATION_LABELS: Record<string, string> = {
  supports: "支持证据",
  similar: "相似工作",
  contradicts: "反证",
  qualifies: "限定性证据",
  overlaps: "部分重合",
  unknown: "关系未确定",
};

const SOURCE_SCOPE_LABELS: Record<string, string> = {
  workspace: "工作区",
  external: "外部论文",
  external_fulltext: "外部论文全文",
};

const EVIDENCE_LEVEL_LABELS: Record<string, string> = {
  full_text: "全文证据",
  metadata_only: "仅有元数据",
};

const VERIFICATION_STATUS_LABELS: Record<string, string> = {
  not_started: "尚未开始",
  in_progress: "核验中",
  incomplete: "证据尚未充分",
  unverified: "未核验",
  verification_incomplete: "核验不完整",
  verified: "核验完成",
  verified_with_warnings: "核验完成（有警告）",
  failed: "核验失败",
  verification_failed: "核验失败",
};

const OPPORTUNITY_STATUS_LABELS: Record<string, string> = {
  candidate: "候选",
  needs_more_evidence: "需要更多证据",
  reviewable_with_warning: "可审阅（有核验警告）",
  confirmed: "已确认",
  edited_confirmed: "编辑后确认",
  rejected: "已驳回",
  deferred: "暂缓处理",
};

const GATE_MESSAGE_LABELS: Record<string, string> = {
  "requires two independent full-text supporting papers": "需要至少两篇相互独立、具有全文证据的支持论文",
  "supporting evidence retrieval status is failed": "支持证据检索失败",
  "counter evidence status is failed": "反证检索失败",
  "similar work retrieval status is failed": "相似工作检索失败",
  "supporting evidence does not cover the opportunity's key problem and hypothesis": "支持证据尚未覆盖该候选的核心问题与假设",
  "external verification did not complete": "外部论文核验未完整完成",
  insufficient_full_text_evidence: "全文证据不足，尚未满足核心证据门槛",
  verified_with_warnings: "核心证据门槛已满足，但仍存在核验警告",
  verified: "核心证据门槛已满足",
  "This metadata-only evidence has no local full-text anchor.": "该证据仅有元数据，本地没有可定位的全文原文。",
};

const LEGACY_GENERATED_TEXT: Record<string, string> = {
  "Investigate the boundary conditions of the claim": "研究该论断成立与失效的边界条件",
  "Investigate the boundary conditions of the topic": "研究该主题成立与失效的边界条件",
  "The claim is plausible but its boundary conditions are not yet established.": "该论断具有一定合理性，但其成立的边界条件尚未明确。",
  "The current evidence does not establish where the observed behavior generalizes.": "现有证据尚不足以确定该现象可推广到哪些条件。",
  "The scope should be narrowed to the datasets, models, and constraints available in this workspace.": "研究范围应限定在当前工作区已有的数据集、模型与约束条件内。",
  "Existing work has not yet been compared under the same conditions.": "现有工作尚未在统一条件下进行充分比较。",
  "Under which conditions does the observed behavior remain reliable?": "在什么条件下，该现象仍然可靠？",
  "The behavior is strongest under the assumptions represented by the workspace evidence.": "在工作区证据所覆盖的假设条件下，该现象预计最为显著。",
  "The effect depends on a measurable data or model condition that can be isolated with an ablation.": "该效应取决于某个可测量的数据或模型条件，并可通过消融实验加以分离验证。",
  "Select datasets and baselines": "选择数据集与基线方法",
  "Compare against the strongest similar-work setting": "与最强的相似工作设置进行比较",
  "Run an ablation for the suspected boundary condition": "针对推测的边界条件开展消融实验",
  "External full-text verification is incomplete.": "外部论文全文核验尚未完成。",
  "External metadata is not a substitute for full-text evidence.": "外部元数据不能替代全文证据。",
  "The current retrieval set may be incomplete.": "当前检索结果可能不完整。",
};

export function discoverStageLabel(stage: string | null | undefined): string {
  if (!stage) return "未知阶段";
  return DISCOVER_STAGE_LABELS[stage as DiscoverStage] ?? stage;
}

export function verificationStatusLabel(status: string | null | undefined): string {
  if (!status) return "未核验";
  return VERIFICATION_STATUS_LABELS[status] ?? status;
}

export function discoverRunVerificationStatusLabel(
  status: string | null | undefined,
  runStatus: string | null | undefined,
  stage: string | null | undefined,
  stageSummaries: Record<string, unknown> | null | undefined,
): string {
  const externalSummary = stageSummaries?.external_search;
  const externalStatus = externalSummary && typeof externalSummary === "object" && !Array.isArray(externalSummary)
    ? (externalSummary as { status?: unknown }).status
    : null;

  if (runStatus === "waiting_for_user" && stage === "external_selection") {
    return status === "failed" ? "外部全文核验失败" : "待选择外部论文";
  }
  if (runStatus === "waiting_for_fulltext" || (stage === "fulltext_verification" && status === "in_progress")) {
    return "等待全文核验";
  }
  if (status === "incomplete") {
    if (externalStatus === "succeeded_partial") return "证据尚未充分（外部检索部分完成）";
    if (externalStatus === "succeeded_empty") return "证据尚未充分（未发现外部候选）";
    return "证据尚未充分";
  }
  return verificationStatusLabel(status);
}

export function opportunityStatusLabel(status: string | null | undefined): string {
  if (!status) return "状态未知";
  return OPPORTUNITY_STATUS_LABELS[status] ?? verificationStatusLabel(status);
}

export function evidenceRelationLabel(relation: string): string {
  return RELATION_LABELS[relation] ?? relation;
}

export function evidenceSourceScopeLabel(scope: string): string {
  return SOURCE_SCOPE_LABELS[scope] ?? scope;
}

export function evidenceLevelDisplayLabel(level: string): string {
  return EVIDENCE_LEVEL_LABELS[level] ?? level;
}

export function gateMessageLabel(message: string): string {
  if (GATE_MESSAGE_LABELS[message]) return GATE_MESSAGE_LABELS[message];
  const failedStatus = message.match(/^(supporting evidence|counter evidence|similar work) retrieval status is (.+)$/);
  if (failedStatus) {
    const subject = failedStatus[1] === "supporting evidence" ? "支持证据" : failedStatus[1] === "counter evidence" ? "反证" : "相似工作";
    return `${subject}检索状态：${getStatusLabel(failedStatus[2])}`;
  }
  return message;
}

function getStatusLabel(status: string): string {
  return ({ failed: "失败", succeeded: "成功", succeeded_partial: "部分成功", succeeded_empty: "成功但无结果" } as Record<string, string>)[status] ?? status;
}

export function localizedGeneratedText(value: string | null | undefined): string {
  if (!value) return "—";
  if (LEGACY_GENERATED_TEXT[value]) return LEGACY_GENERATED_TEXT[value];
  const questionPrefix = "When does the following claim hold, and when does it fail? ";
  if (value.startsWith(questionPrefix)) {
    return `以下论断在什么条件下成立，又会在什么条件下失效？${value.slice(questionPrefix.length)}`;
  }
  const evidenceMatch = value.match(/^The workspace returned (\d+) supporting, (\d+) similar-work, and (\d+) counter-evidence passages, but the final evidence gate is incomplete\.$/);
  if (evidenceMatch) {
    return `工作区检索到 ${evidenceMatch[1]} 条支持证据、${evidenceMatch[2]} 条相似工作证据和 ${evidenceMatch[3]} 条反证，但最终证据门槛尚未满足。`;
  }
  return value;
}
