export interface EvidenceOffsetSpan {
  start_char?: number | null;
  end_char?: number | null;
  relation?: string | null;
}

export interface NormalizedEvidenceSpan {
  start: number;
  end: number;
  relation?: string | null;
}

export interface EvidenceExcerpt {
  content: string;
  spans: EvidenceOffsetSpan[];
  start_offset: number;
  end_offset: number;
  omitted_before: boolean;
  omitted_after: boolean;
  is_excerpt: boolean;
}

const DEFAULT_CONTEXT_CHARS = 320;
const LINE_BOUNDARY_TOLERANCE = 120;

export function normalizeEvidenceSpan(
  content: string,
  span: EvidenceOffsetSpan,
): NormalizedEvidenceSpan | null {
  const start = span.start_char;
  const end = span.end_char;
  if (
    !Number.isInteger(start)
    || !Number.isInteger(end)
    || start == null
    || end == null
    || start < 0
    || end <= start
    || end > content.length
  ) {
    return null;
  }
  return { start, end, relation: span.relation };
}

function relativeSpan(span: NormalizedEvidenceSpan, startOffset: number): EvidenceOffsetSpan {
  return {
    start_char: span.start - startOffset,
    end_char: span.end - startOffset,
    relation: span.relation,
  };
}

/**
 * 围绕一个精确 EvidenceSpan 构建有界视图。偏移量会转换为返回摘录中的位置，
 * 因此调用方可以继续使用同一个高亮器，无需再次搜索重复的证据文本。
 */
export function buildEvidenceExcerpt(
  content: string,
  spans: EvidenceOffsetSpan[],
  focusSpan?: EvidenceOffsetSpan,
  contextChars = DEFAULT_CONTEXT_CHARS,
): EvidenceExcerpt {
  const validSpans = spans
    .map((span) => normalizeEvidenceSpan(content, span))
    .filter((span): span is NormalizedEvidenceSpan => span !== null);
  const focus = focusSpan ? normalizeEvidenceSpan(content, focusSpan) : validSpans[0] ?? null;

  if (!focus) {
    return {
      content,
      spans: [],
      start_offset: 0,
      end_offset: content.length,
      omitted_before: false,
      omitted_after: false,
      is_excerpt: false,
    };
  }

  const safeContextChars = Math.max(0, Math.floor(contextChars));
  let startOffset = Math.max(0, focus.start - safeContextChars);
  let endOffset = Math.min(content.length, focus.end + safeContextChars);

  const previousLineBreak = content.lastIndexOf("\n", startOffset - 1);
  if (previousLineBreak >= 0 && startOffset - previousLineBreak <= LINE_BOUNDARY_TOLERANCE) {
    startOffset = previousLineBreak + 1;
  }
  const nextLineBreak = content.indexOf("\n", endOffset);
  if (nextLineBreak >= 0 && nextLineBreak - endOffset <= LINE_BOUNDARY_TOLERANCE) {
    endOffset = nextLineBreak;
  }

  const visibleSpans = [...validSpans, focus]
    .filter((span, index, all) => all.findIndex((candidate) => candidate.start === span.start && candidate.end === span.end) === index)
    .filter((span) => span.start < endOffset && span.end > startOffset)
    .map((span) => relativeSpan(span, startOffset));

  return {
    content: content.slice(startOffset, endOffset),
    spans: visibleSpans,
    start_offset: startOffset,
    end_offset: endOffset,
    omitted_before: startOffset > 0,
    omitted_after: endOffset < content.length,
    is_excerpt: startOffset > 0 || endOffset < content.length,
  };
}
