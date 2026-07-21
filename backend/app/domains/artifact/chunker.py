"""Text chunker: split ParsedPdf into retrieval-friendly chunks.

Strategy (per data_contracts.md Contract #1):
  1. Split by section boundaries first (from SectionMarker)
  2. Within a section, split by paragraph (double newline)
  3. If a paragraph is too long, split by sentence
  4. Merge too-short chunks with neighbors (min 100 tokens)
  5. Hard cap at 800 tokens (force split mid-sentence if needed)
  6. Adjacent chunks overlap by ~50 tokens

Target: 512 ± 50 tokens per chunk.

Token estimation: we use a cheap heuristic (1 token ≈ 4 chars) for speed.
The contract allows `tokens_estimate` to be approximate; the actual
embedding model (BGE-m3) will tokenize properly. If you need exact counts,
swap `_estimate_tokens` to use the real tokenizer.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.domains.artifact.pdf_parser import ParsedPdf, get_page_for_char_offset


# Tunable parameters
TARGET_TOKENS = 512
MIN_TOKENS = 100
MAX_TOKENS = 800
OVERLAP_TOKENS = 50
CHARS_PER_TOKEN = 4  # cheap heuristic

TARGET_CHARS = TARGET_TOKENS * CHARS_PER_TOKEN  # ~2048
MIN_CHARS = MIN_TOKENS * CHARS_PER_TOKEN  # ~400
MAX_CHARS = MAX_TOKENS * CHARS_PER_TOKEN  # ~3200
OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN  # ~200


@dataclass
class Chunk:
    """A single chunk ready for JSONL export (Contract #1)."""

    chunk_id: str
    workspace_id: str
    paper_id: str
    artifact_id: str
    chunk_index: int
    section: str | None
    subsection: str | None
    text: str
    start_char: int
    end_char: int
    page_start: int
    page_end: int
    tokens_estimate: int
    chunk_version: str
    created_at: str  # ISO 8601


def chunk_parsed_pdf(
    parsed: ParsedPdf,
    *,
    workspace_id: str,
    paper_id: str,
    artifact_id: str,
    created_at: str,
    chunk_version: str = "v1",
) -> list[Chunk]:
    """Split a ParsedPdf into Chunks following Contract #1."""
    if not parsed.full_text.strip():
        return []

    # Build section boundaries: list of (start_char, section_name, subsection).
    # The "end" of each section is the start of the next.
    section_starts: list[tuple[int, str, str | None]] = []
    for sm in parsed.sections:
        section_starts.append((sm.char_offset, sm.section, sm.subsection))
    # Add a final boundary at end of text with section="Unknown" so the
    # last section's content gets captured.
    section_starts.append((len(parsed.full_text), "Unknown", None))

    # If no sections detected, treat whole doc as one "Unknown" section.
    if len(section_starts) == 1:
        section_starts.insert(0, (0, "Unknown", None))

    chunks: list[Chunk] = []
    chunk_index = 0

    for i in range(len(section_starts) - 1):
        sec_start, sec_name, sec_sub = section_starts[i]
        sec_end = section_starts[i + 1][0]
        # Skip empty sections.
        section_text = parsed.full_text[sec_start:sec_end]
        if not section_text.strip():
            continue

        # Step 1: split section into paragraphs.
        paragraphs = _split_paragraphs(section_text)

        # Step 2: merge small paragraphs up to TARGET_CHARS.
        merged_paras = _merge_paragraphs(paragraphs)

        # Step 3: for each merged paragraph, split further if too long.
        for para_text, para_offset_in_section in merged_paras:
            pieces = _split_long_text(para_text)

            # Step 4: add overlap between consecutive pieces.
            pieces_with_overlap = _add_overlap(pieces)

            for piece_text, piece_start_in_para in pieces_with_overlap:
                if not piece_text.strip():
                    continue
                # Compute absolute char offsets
                abs_start = sec_start + para_offset_in_section + piece_start_in_para
                abs_end = abs_start + len(piece_text)
                # Clamp to document bounds.
                abs_end = min(abs_end, len(parsed.full_text))

                page_start = get_page_for_char_offset(parsed, abs_start)
                page_end = get_page_for_char_offset(parsed, abs_end - 1) if abs_end > abs_start else page_start
                if page_end == 0:
                    page_end = page_start
                if page_start == 0:
                    page_start = page_end

                tokens_est = _estimate_tokens(piece_text)

                chunks.append(
                    Chunk(
                        chunk_id=str(uuid.uuid4()),
                        workspace_id=workspace_id,
                        paper_id=paper_id,
                        artifact_id=artifact_id,
                        chunk_index=chunk_index,
                        section=sec_name,
                        subsection=sec_sub,
                        text=piece_text.strip(),
                        start_char=abs_start,
                        end_char=abs_end,
                        page_start=page_start,
                        page_end=page_end,
                        tokens_estimate=tokens_est,
                        chunk_version=chunk_version,
                        created_at=created_at,
                    )
                )
                chunk_index += 1

    # Final pass: merge tiny trailing chunks into the previous one if both
    # belong to the same section. This avoids 50-token orphans.
    chunks = _merge_tiny_tail_chunks(chunks)

    # Re-index chunk_index after any merging.
    for i, c in enumerate(chunks):
        c.chunk_index = i

    return chunks


# ----------------------------------------------------------------- helpers
def _estimate_tokens(text: str) -> int:
    """Cheap token estimate: ~4 chars per token for English academic text."""
    # BGE-m3 / cl100k_base average ~4 chars/token for English.
    # Round up so we err on the conservative side.
    return max(1, len(text) // CHARS_PER_TOKEN)


def _split_paragraphs(text: str) -> list[tuple[str, int]]:
    """Split text into paragraphs by double-newline. Return (text, offset) pairs."""
    result: list[tuple[str, int]] = []
    cursor = 0
    for raw_para in re.split(r"\n\s*\n", text):
        if not raw_para:
            # Account for the separator we consumed
            continue
        # Find this paragraph's actual position in the original text.
        offset = text.find(raw_para, cursor)
        if offset < 0:
            offset = cursor
        # Include any leading whitespace we may have stripped by re.split
        result.append((raw_para, offset))
        cursor = offset + len(raw_para)
    return result


def _merge_paragraphs(
    paras: list[tuple[str, int]],
) -> list[tuple[str, int]]:
    """Merge consecutive paragraphs until we reach TARGET_CHARS."""
    if not paras:
        return []
    merged: list[tuple[str, int]] = []
    buf_text = ""
    buf_offset = 0

    for text, offset in paras:
        if not buf_text:
            buf_text = text
            buf_offset = offset
        elif len(buf_text) + len(text) + 2 <= TARGET_CHARS:
            # Join with double newline to preserve paragraph boundary.
            buf_text = buf_text + "\n\n" + text
        else:
            merged.append((buf_text, buf_offset))
            buf_text = text
            buf_offset = offset
    if buf_text:
        merged.append((buf_text, buf_offset))
    return merged


def _split_long_text(text: str) -> list[tuple[str, int]]:
    """If text is too long, split by sentence. Returns (text, offset) pairs.

    Offsets are relative to the start of `text`.
    """
    if len(text) <= MAX_CHARS:
        return [(text, 0)]

    # Split into sentences using a simple regex.
    # This is intentionally simple - we don't need perfect sentence
    # boundaries, just reasonable split points.
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)

    pieces: list[tuple[str, int]] = []
    buf_text = ""
    buf_offset = 0
    cursor = 0

    for sentence in sentences:
        if not buf_text:
            buf_text = sentence
            buf_offset = cursor
        elif len(buf_text) + len(sentence) + 1 <= TARGET_CHARS:
            buf_text = buf_text + " " + sentence
        else:
            pieces.append((buf_text, buf_offset))
            cursor = buf_offset + len(buf_text) + 1
            buf_text = sentence
            buf_offset = cursor
        cursor += len(sentence) + 1  # +1 for the separator we consumed

    if buf_text:
        pieces.append((buf_text, buf_offset))

    # If any piece is still > MAX_CHARS (very long sentence), hard-split by chars.
    final: list[tuple[str, int]] = []
    for text_piece, off in pieces:
        if len(text_piece) <= MAX_CHARS:
            final.append((text_piece, off))
        else:
            # Hard split at word boundaries near MAX_CHARS.
            i = 0
            while i < len(text_piece):
                end = min(i + MAX_CHARS, len(text_piece))
                # Walk back to a space to avoid cutting mid-word.
                if end < len(text_piece):
                    space_at = text_piece.rfind(" ", i, end)
                    if space_at > i + MIN_CHARS:
                        end = space_at
                final.append((text_piece[i:end], off + i))
                i = end
                # Skip the space.
                if i < len(text_piece) and text_piece[i] == " ":
                    i += 1
    return final


def _add_overlap(pieces: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Add overlap to each piece (except the first) by prepending the tail of the previous piece."""
    if len(pieces) <= 1:
        return pieces
    result: list[tuple[str, int]] = [pieces[0]]
    for i in range(1, len(pieces)):
        prev_text, _ = pieces[i - 1]
        cur_text, cur_offset = pieces[i]
        # Take up to OVERLAP_CHARS from the end of prev_text, walking back
        # to a word boundary for cleanliness.
        overlap_text = prev_text[-OVERLAP_CHARS:]
        space_idx = overlap_text.find(" ")
        if space_idx > 0:
            overlap_text = overlap_text[space_idx + 1 :]
        # Adjust offset to point to the start of the overlap text.
        adjusted_offset = cur_offset - len(overlap_text) - 1  # -1 for the space
        if adjusted_offset < 0:
            adjusted_offset = 0
            overlap_text = prev_text  # fallback, but this shouldn't happen
        result.append((overlap_text + " " + cur_text, adjusted_offset))
    return result


def _merge_tiny_tail_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """If the last chunk in a section is < MIN_TOKENS, merge into previous."""
    if len(chunks) < 2:
        return chunks
    result = [chunks[0]]
    for c in chunks[1:]:
        prev = result[-1]
        if (
            c.tokens_estimate < MIN_TOKENS
            and c.section == prev.section
            and prev.tokens_estimate + c.tokens_estimate < MAX_TOKENS
        ):
            # Merge: extend prev's text and end_char.
            prev.text = prev.text + "\n\n" + c.text
            prev.end_char = c.end_char
            prev.page_end = c.page_end
            prev.tokens_estimate = _estimate_tokens(prev.text)
        else:
            result.append(c)
    return result
