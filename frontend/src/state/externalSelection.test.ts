import { describe, expect, it } from "vitest";
import {
  canSelectExternalCandidate,
  externalCandidateActionLabel,
  externalSelectionIsOpen,
} from "./externalSelection";

describe("external paper selection state", () => {
  it("allows selection only while the run explicitly waits for external papers", () => {
    expect(externalSelectionIsOpen("waiting_for_user", "external_selection")).toBe(true);
    expect(canSelectExternalCandidate("waiting_for_user", "external_selection", "unverified")).toBe(true);
    expect(canSelectExternalCandidate("waiting_for_fulltext", "fulltext_verification", "unverified")).toBe(false);
    expect(canSelectExternalCandidate("succeeded", "saved", "unverified")).toBe(false);
  });

  it("never allows candidates that are already selected, processing, or verified", () => {
    expect(canSelectExternalCandidate("waiting_for_user", "external_selection", "selected")).toBe(false);
    expect(canSelectExternalCandidate("waiting_for_user", "external_selection", "imported_pending_parse")).toBe(false);
    expect(canSelectExternalCandidate("waiting_for_user", "external_selection", "verified")).toBe(false);
  });

  it("explains why a stale action cannot be used", () => {
    expect(externalCandidateActionLabel("waiting_for_fulltext", "fulltext_verification", "unverified")).toBe("未选择（当前批次处理中）");
    expect(externalCandidateActionLabel("waiting_for_fulltext", "fulltext_verification", "no_pdf")).toBe("本次核验未完成");
    expect(externalCandidateActionLabel("succeeded", "saved", "unverified")).toBe("本轮未选择");
    expect(externalCandidateActionLabel("waiting_for_user", "external_selection", "import_failed")).toBe("重新获取 PDF 并核验");
  });
});
