"""Split long parsed-markdown documents into LLM-sized batches.

The extraction prompt targets the whole paper, but the configured remote
model's context window
forces us to chunk. The strategy here:

  * keep paragraphs / headings (``\n## ``, ``\n\n``) intact at split
    boundaries so the LLM doesn't lose semantic structure;
  * tail batches always end on a real document character offset — never
    silently drop the last paragraph;
  * overlap batches by ``overlap_chars`` so cross-batch entity resolution
    still works (a method mentioned at the tail of batch N can reappear
    in batch N+1).

The function is intentionally side-effect free — pure string math — so it
can be unit-tested without spinning up a DB or an LLM client.
"""

from __future__ import annotations

# Why these defaults:
#   * ``max_chars`` (40 000): a bounded slice for the configured remote model
#     while leaving room for the prompt scaffold and the JSON response.
#   * ``overlap_chars`` (1 000): large enough to re-surface a tail method,
#     small enough not to balloon token spend.
DEFAULT_MAX_CHARS = 40_000
DEFAULT_OVERLAP_CHARS = 1_000


def split_extraction_batches(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[tuple[int, str]]:
    """Return ``[(start_offset, batch_text), ...]`` covering ``text``.

    Each tuple's ``start_offset`` is the absolute character position of
    ``batch_text[0]`` in the original document, so callers can resolve
    evidence spans back to the master text.
    """
    if len(text) <= max_chars:
        return [(0, text)]

    batches: list[tuple[int, str]] = []
    start = 0
    while start < len(text):
        hard_end = min(start + max_chars, len(text))
        end = hard_end
        if hard_end < len(text):
            min_split = start + max_chars // 2
            heading = text.rfind("\n## ", min_split, hard_end)
            paragraph = text.rfind("\n\n", min_split, hard_end)
            split_at = heading if heading >= min_split else paragraph
            if split_at >= min_split:
                end = split_at
        if end <= start:
            end = hard_end
        batches.append((start, text[start:end]))
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return batches


__all__ = ["split_extraction_batches", "DEFAULT_MAX_CHARS", "DEFAULT_OVERLAP_CHARS"]
