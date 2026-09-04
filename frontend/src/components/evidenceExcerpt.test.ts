import { describe, expect, it } from "vitest";
import { buildEvidenceExcerpt } from "./evidenceExcerpt";

describe("buildEvidenceExcerpt", () => {
  it("keeps the exact highlighted span while bounding surrounding context", () => {
    const content = "prefix ".repeat(80) + "TARGET EVIDENCE" + " suffix".repeat(80);
    const start = content.indexOf("TARGET EVIDENCE");
    const excerpt = buildEvidenceExcerpt(
      content,
      [{ start_char: start, end_char: start + "TARGET EVIDENCE".length }],
      { start_char: start, end_char: start + "TARGET EVIDENCE".length },
      20,
    );

    expect(excerpt.is_excerpt).toBe(true);
    expect(excerpt.content).toContain("TARGET EVIDENCE");
    expect(excerpt.content.length).toBeLessThan(content.length);
    expect(excerpt.spans).toEqual([{
      start_char: excerpt.content.indexOf("TARGET EVIDENCE"),
      end_char: excerpt.content.indexOf("TARGET EVIDENCE") + "TARGET EVIDENCE".length,
      relation: undefined,
    }]);
  });

  it("falls back to the full content without a valid offset", () => {
    const content = "full source text";
    const excerpt = buildEvidenceExcerpt(content, [{ start_char: null, end_char: null }]);

    expect(excerpt.content).toBe(content);
    expect(excerpt.spans).toEqual([]);
    expect(excerpt.is_excerpt).toBe(false);
  });
});
